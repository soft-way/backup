import mock
from twindb_backup.ls import list_available_backups


@mock.patch('twindb_backup.ls.os')
@mock.patch('twindb_backup.ls.get_destination')
@mock.patch('twindb_backup.ls.Popen')
def test_list_available_backups_ssh(mock_popen, mock_get_destination, mock_os):

    mock_os.path.exists.return_value = True

    mock_config = mock.Mock()
    mock_config.get.return_value = "/foo/bar"

    mock_dst = mock.Mock()
    mock_dst.remote_path = '/foo/bar'

    mock_get_destination.return_value = mock_dst
    mock_dst.find_files.return_value = []

    list_available_backups(mock_config)
    mock_popen.assert_called_once_with(["find", '/foo/bar', '-type', 'f'])

    calls = [
        mock.call('/foo/bar', 'hourly'),
        mock.call('/foo/bar', 'daily'),
        mock.call('/foo/bar', 'weekly'),
        mock.call('/foo/bar', 'monthly'),
        mock.call('/foo/bar', 'yearly'),
    ]
    mock_dst.find_files.assert_has_calls(calls)
