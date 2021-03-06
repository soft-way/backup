# -*- coding: utf-8 -*-
"""Module that parses config file, builds a modifiers chain and fires
backup jobs.
"""
import ConfigParser
import base64
import errno
import fcntl
import os
import signal
import time
import traceback
from contextlib import contextmanager
from resource import getrlimit, RLIMIT_NOFILE, setrlimit
from twindb_backup import (
    LOG, get_directories_to_backup, get_timeout, LOCK_FILE,
    TwinDBBackupError, save_measures)
from twindb_backup.configuration import get_destination
from twindb_backup.export import export_info
from twindb_backup.exporter.base_exporter import ExportCategory, \
    ExportMeasureType
from twindb_backup.modifiers.base import ModifierException
from twindb_backup.modifiers.gpg import Gpg
from twindb_backup.modifiers.gzip import Gzip
from twindb_backup.modifiers.keeplocal import KeepLocal
from twindb_backup.source.file_source import FileSource
from twindb_backup.source.mysql_source import MySQLSource, MySQLConnectInfo


def _backup_stream(config, src, dst, callbacks=None):
    stream = src.get_stream()
    src_name = src.get_name()
    # Gzip modifier
    stream = Gzip(stream).get_stream()
    src_name += '.gz'
    # KeepLocal modifier
    try:
        keep_local_path = config.get('destination', 'keep_local_path')
        kl_modifier = KeepLocal(stream,
                                os.path.join(keep_local_path, src_name))
        stream = kl_modifier.get_stream()
        if callbacks is not None:
            callbacks.append((kl_modifier, {
                'keep_local_path': keep_local_path,
                'dst': dst
            }))
    except ConfigParser.NoOptionError:
        LOG.debug('keep_local_path is not present in the config file')
    # GPG modifier
    try:
        stream = Gpg(stream,
                     config.get('gpg', 'recipient'),
                     config.get('gpg', 'keyring')).get_stream()
        src_name += '.gpg'
    except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
        pass
    except ModifierException as err:
        LOG.warning(err)
        LOG.warning('Will skip encryption')
    if not dst.save(stream, src_name):
        LOG.error('Failed to save backup copy %s', src_name)
        exit(1)
    return src_name


def backup_files(run_type, config):
    """Backup local directories

    :param run_type: Run type
    :type run_type: str
    :param config: Configuration
    :type config: ConfigParser.ConfigParser
    """
    backup_start = time.time()
    for directory in get_directories_to_backup(config):
        LOG.debug('copying %s', directory)
        src = FileSource(directory, run_type)
        dst = get_destination(config)
        _backup_stream(config, src, dst)
        src.apply_retention_policy(dst, config, run_type)
    export_info(config, data=time.time() - backup_start,
                category=ExportCategory.files,
                measure_type=ExportMeasureType.backup)


def backup_mysql(run_type, config):
    """Take backup of local MySQL instance

    :param run_type: Run type
    :type run_type: str
    :param config: Tool configuration
    :type config: ConfigParser.ConfigParser
    :return: None
    """
    try:
        if not config.getboolean('source', 'backup_mysql'):
            raise TwinDBBackupError('MySQL backups are not enabled in config')

    except (ConfigParser.NoOptionError, TwinDBBackupError) as err:
        LOG.debug(err)
        LOG.debug('Not backing up MySQL')
        return

    dst = get_destination(config)

    try:
        full_backup = config.get('mysql', 'full_backup')
    except ConfigParser.NoOptionError:
        full_backup = 'daily'
    backup_start = time.time()
    src = MySQLSource(MySQLConnectInfo(config.get('mysql',
                                                  'mysql_defaults_file')),
                      run_type,
                      full_backup,
                      dst)

    callbacks = []
    src_name = _backup_stream(config, src, dst, callbacks)
    status = prepare_status(dst, src, run_type, src_name, backup_start)
    status = src.apply_retention_policy(dst, config, run_type, status)
    backup_duration = \
        status[run_type][src_name]['backup_finished'] - \
        status[run_type][src_name]['backup_started']
    export_info(config, data=backup_duration,
                category=ExportCategory.mysql,
                measure_type=ExportMeasureType.backup)
    dst.status(status)

    LOG.debug('Callbacks are %r', callbacks)
    for callback in callbacks:
        callback[0].callback(**callback[1])


def prepare_status(dst, src, run_type, src_name, backup_start):
    """Prepare status for update"""
    status = dst.status()
    status[run_type][src_name] = {
        'binlog': src.binlog_coordinate[0],
        'position': src.binlog_coordinate[1],
        'lsn': src.lsn,
        'type': src.type,
        'backup_started': backup_start,
        'backup_finished': time.time(),
        'config': []
    }
    for path, content in src.get_my_cnf():
        status[run_type][src_name]['config'].append({
            path: base64.b64encode(content)
        })

    if src.incremental:
        status[run_type][src_name]['parent'] = src.parent

    if src.galera:
        status[run_type][src_name]['wsrep_provider_version'] = \
            src.wsrep_provider_version
    return status


def set_open_files_limit():
    """Detect maximum supported number of open file and set it"""
    max_files = getrlimit(RLIMIT_NOFILE)[0]
    while True:
        try:
            setrlimit(RLIMIT_NOFILE, (max_files, max_files))
            max_files += 1
        except ValueError:
            break
    LOG.debug('Setting max files limit to %d', max_files)


def backup_everything(run_type, config):
    """
    Run backup job

    :param run_type: hourly, daily, etc
    :type run_type: str
    :param config: ConfigParser instance
    :type config: ConfigParser.ConfigParser
    """
    set_open_files_limit()

    try:
        backup_start = time.time()
        backup_files(run_type, config)
        backup_mysql(run_type, config)
        end = time.time()
        save_measures(backup_start, end)
    except ConfigParser.NoSectionError as err:
        LOG.error(err)
        exit(1)


@contextmanager
def timeout(seconds):
    """
    Implement timeout

    :param seconds: timeout in seconds
    :type seconds: int
    """

    def timeout_handler(signum, frame):
        """Function to call on a timeout event"""
        if signum or frame:
            pass

    original_handler = signal.signal(signal.SIGALRM, timeout_handler)

    try:
        signal.alarm(seconds)
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, original_handler)


def run_backup_job(cfg, run_type, lock_file=LOCK_FILE):
    """
    Grab a lock waiting up to allowed timeout and start backup jobs

    :param cfg: Tool configuration
    :type cfg: ConfigParser.ConfigParser
    :param run_type: Run type
    :type run_type: str
    :param lock_file: File used as a lock
    :type lock_file: str
    """
    with timeout(get_timeout(run_type)):
        try:
            file_desriptor = open(lock_file, 'w')
            fcntl.flock(file_desriptor, fcntl.LOCK_EX)
            LOG.debug(run_type)
            if cfg.getboolean('intervals', "run_%s" % run_type):
                backup_everything(run_type, cfg)
            else:
                LOG.debug('Not running because run_%s is no', run_type)
        except IOError as err:
            if err.errno != errno.EINTR:
                LOG.debug(traceback.format_exc())
                raise err
            msg = 'Another instance of twindb-backup is running?'
            if run_type == 'hourly':
                LOG.debug(msg)
            else:
                LOG.error(msg)
