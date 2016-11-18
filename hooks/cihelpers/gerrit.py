import logging
import os
import sys
import subprocess
import json

from charmhelpers.core.hookenv import (
    log as _log,
    ERROR,
)
try:
    import paramiko
except ImportError:
    if sys.version_info.major == 2:
        subprocess.check_call(['apt-get', 'install', '-y', 'python-paramiko'])
    else:
        subprocess.check_call(['apt-get', 'install', '-y', 'python3-paramiko'])
    import paramiko

_connection = None
GERRIT_DAEMON = "/etc/init.d/gerrit"

logging.basicConfig(level=logging.INFO)


def log(msg, level=None):
    # wrap log calls and distribute to correct logger
    # depending if this code is being run by a hook
    # or an external script.
    if os.getenv('JUJU_AGENT_SOCKET'):
        _log(msg, level=level)
    else:
        logging.info(msg)


def get_ssh(host, user, port, key_file):
    global _connection
    if _connection:
        return _connection

    _connection = paramiko.SSHClient()
    _connection.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    _connection.connect(host, username=user, port=port, key_filename=key_file)

    return _connection


# start gerrit application
def start_gerrit():
    try:
        subprocess.check_call([GERRIT_DAEMON, "start"])
    except:
        pass


# stop gerrit application
def stop_gerrit():
    try:
        subprocess.check_call([GERRIT_DAEMON, "stop"])
    except:
        pass


class GerritException(Exception):
    def __init__(self, msg):
        log('Failed to execute gerrit command: %s' % msg)
        super(GerritException, self).__init__(msg)


class GerritClient(object):
    def __init__(self, host, user, port, key_file):
        self.ssh = get_ssh(host, user, port, key_file)

    def _run_cmd(self, cmd):
        stdin, stdout, stderr = self.ssh.exec_command(cmd)
        return (stdout.read(), stderr.read())

    def create_user(self, user, name, group, ssh_key):
        log('Creating gerrit new user %s in group %s.' % (user, group))
        cmd = ('gerrit create-account %(user)s --full-name "%(name)s" '
               '--group "%(group)s" --ssh-key '
               '"%(ssh_key)s"' % locals())
        stdout, stderr = self._run_cmd(cmd)
        if not stdout and not stderr:
            log('Created new gerrit user %s in group %s.' % (user, group))

        if stderr.startswith('fatal'):
            if 'already exists' not in stderr:
                # different error
                log('Error creating account', ERROR)
                sys.exit(1)
            else:
                # retrieve user id and update keys
                account_id = None
                cmd = ('gerrit gsql --format json -c "SELECT account_id '
                    'FROM account_external_ids WHERE external_id=\'username:%s\'"'
                    % (user))
                stdout, stderr = self._run_cmd(cmd)
                if not stderr:
                    # load and decode json, extract account id
                    lines = stdout.splitlines()
                    if len(lines)>0:
                        res = json.loads(lines[0])
                        try:
                            account_id = res['columns']['account_id']
                        except:
                            pass

                # if found, update ssh keys
                if account_id:
                    cmd = ('gerrit gsql -c "DELETE FROM account_ssh_keys '
                           'WHERE account_id=%s' % account_id)
                    stdout, stderr = self._run_cmd(cmd)

                    # insert new key
                    cmd = ('gerrit gsql -c "INSERT INTO account_ssh_keys '
                        '(ssh_public_key, valid, account_id, seq) VALUES (\'%s\', \'Y\', '
                        '\'%s\', 0)" ' % (ssh_key, account_id))
                    stdout, stderr = self._run_cmd(cmd)

        # reboot gerrit to refresh accounts
        stop_gerrit()
        start_gerrit()

    def create_users_batch(self, group, users):
        for user in users:
            # sets container user, name, ssh, openid
            login = user[0]
            name = user[1]
            email = user[2]
            ssh = user[3]
            openid = user[4]

            cmd = ('gerrit create-account %s --full-name "%s" '
                   '--group "%s" --email "%s"' %
                   (login, name, group, email))
            stdout, stderr = self._run_cmd(cmd)

            if stderr.startswith('fatal'):
                if 'already exists' not in stderr:
                    sys.exit(1)

            # retrieve user id
            account_id = None
            cmd = ('gerrit gsql --format json -c "SELECT account_id '
                'FROM account_external_ids WHERE external_id=\'username:%s\'"'
                % (login))
            stdout, stderr = self._run_cmd(cmd)
            if not stderr:
                # load and decode json, extract account id
                lines = stdout.splitlines()
                if len(lines)>0:
                    res = json.loads(lines[0])
                    try:
                        account_id = res['columns']['account_id']
                    except:
                        pass

            # if found, update ssh keys and openid
            if account_id:
                # remove old keys and add new
                if len(ssh)>0:
                    cmd = ('gerrit gsql -c "DELETE FROM account_ssh_keys '
                           'WHERE account_id=%s AND ssh_public_key NOT IN (%s)"' %
                           (account_id, (', '.join('\''+item+'\'' for item in ssh)) ))
                else:
                    cmd = ('gerrit gsql -c "DELETE FROM account_ssh_keys '
                           'WHERE account_id=%s' % account_id)

                stdout, stderr = self._run_cmd(cmd)

                num_key = 0
                for ssh_key in ssh:
                    # insert new keys
                    cmd = ('gerrit gsql -c "INSERT INTO account_ssh_keys '
                        '(ssh_public_key, valid, account_id, seq) SELECT '
                        '%(ssh_key)s, %(valid)s, %(account_id)s, %(num_key)s '
                        'WHERE NOT EXISTS (SELECT '
                        'account_id FROM account_ssh_keys WHERE '
                        'account_id=%(account_id)s AND ssh_public_key=%(ssh_key)s)"' %
                        {'ssh_key': '\''+ssh_key+'\'', 'valid':'\'Y\'',
                         'account_id': '\''+account_id+'\'', 'num_key': num_key})
                    num_key+=1
                    stdout, stderr = self._run_cmd(cmd)

                # replace external id
                if openid:
                    openid = openid.replace('login.launchpad.net', 'login.ubuntu.com')
                    cmd = ('gerrit gsql -c "DELETE FROM account_external_ids '
                           'WHERE account_id=%s AND external_id NOT IN (%s) AND '
                           'external_id LIKE \'http%%\'"' % (account_id, '\''+openid+'\''))
                    stdout, stderr = self._run_cmd(cmd)

                    # replace launchpad for ubuntu account
                    cmd = ('gerrit gsql -c "INSERT INTO account_external_ids '
                           '(account_id, email_address, external_id) SELECT '
                           '%(account_id)s, %(email_address)s, %(external_id)s WHERE '
                           'NOT EXISTS (SELECT account_id FROM account_external_ids '
                           'WHERE account_id=%(account_id)s AND external_id=%(external_id)s)"' %
                           {'account_id':'\''+account_id+'\'',
                           'email_address':'\''+str(email)+'\'',
                           'external_id': '\''+openid+'\''})
                    stdout, stderr = self._run_cmd(cmd)


    def create_project(self, project):
        log('Creating gerrit project %s' % project)
        cmd = ('gerrit create-project %s' % project)
        stdout, stderr = self._run_cmd(cmd)
        if not stdout and not stderr:
            log('Created new project %s.' % project)
            return True
        else:
            log('Error creating project %s, skipping project creation' %
                project)
            return False

    def create_group(self, group):
        log('Creating gerrit group %s' % group)
        cmd = ('gerrit create-group %s' % group)
        stdout, stderr = self._run_cmd(cmd)
        if not stdout and not stderr:
            log('Created new group %s.' % group)

    def flush_cache(self):
        cmd = ('gerrit flush-caches')
        stdout, stderr = self._run_cmd(cmd)
