import os
import boto3 as boto3
from twindb_backup import log
from twindb_backup.destination.base_destination import BaseDestination, \
    DestinationError


class S3Error(DestinationError):
    pass


class S3(BaseDestination):
    def __init__(self, bucket, access_key_id, secret_access_key,
                 default_region='us-east-1'):
        super(S3, self).__init__()
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.default_region = default_region
        os.environ["AWS_ACCESS_KEY_ID"] = self.access_key_id
        os.environ["AWS_SECRET_ACCESS_KEY"] = self.secret_access_key
        os.environ["AWS_DEFAULT_REGION"] = self.default_region

    def save(self, handler, name, keep_local=None):
        """
        Read from handler and save it to Amazon S3

        :param keep_local: save backup copy in this directory
        :param name: save backup copy in a file with this name
        :param handler: stdout handler from backup source
        :return: exit code
        """
        remote_name = "s3://{bucket}/{name}".format(
            bucket=self.bucket,
            name=name
        )
        cmd = ["aws", "s3", "cp", "-", remote_name]
        return self._save(cmd, handler, keep_local, name)

    def list_files(self, prefix):
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(self.bucket)
        log.debug('Listing s3://%s/%s', bucket.name, prefix)
        return sorted(bucket.objects.filter(Prefix=prefix))

    def delete(self, obj):
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(self.bucket)
        log.debug('deleting {0}:{1}'.format(bucket.name, obj.key))
        obj.delete()