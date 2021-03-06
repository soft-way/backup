import socket

import pytest
import time


from tests.integration.conftest import get_container
from twindb_backup import LOG, setup_logging


# noinspection PyShadowingNames
@pytest.yield_fixture
def master1(docker_client, container_network):

    bootstrap_script = '/twindb-backup/support/bootstrap/master1.sh'
    container = get_container(
        'master1',
        bootstrap_script,
        docker_client,
        container_network,
        1
    )

    timeout = time.time() + 30 * 60

    while time.time() < timeout:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sock.connect_ex((container['ip'], 3306)) == 0:
            break
        time.sleep(1)

    raw_container = docker_client.containers.get(container['Id'])
    privileges_file = "/twindb-backup/vagrant/environment/puppet/" \
                      "modules/profile/files/mysql_grants.sql"
    raw_container.exec_run('bash -c "mysql mysql < %s"'
                           % privileges_file)

    yield container
    if container:
        LOG.info('Removing container %s', container['Id'])
        docker_client.api.remove_container(container=container['Id'],
                                           force=True)


# noinspection PyShadowingNames
@pytest.yield_fixture
def master2(docker_client, container_network):

    bootstrap_script = '/twindb-backup/support/bootstrap/master2.sh'
    container = get_container(
        'master2',
        bootstrap_script,
        docker_client,
        container_network,
        2
    )
    timeout = time.time() + 30 * 60
    while time.time() < timeout:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sock.connect_ex((container['ip'], 22)) == 0:
            break
        time.sleep(1)

    yield container
    if container:
        LOG.info('Removing container %s', container['Id'])
        docker_client.api.remove_container(container=container['Id'],
                                           force=True)


@pytest.fixture
def config_content_clone():
    return """

[ssh]
ssh_user=root
ssh_key={PRIVATE_KEY}

[mysql]
mysql_defaults_file={MY_CNF}
"""
