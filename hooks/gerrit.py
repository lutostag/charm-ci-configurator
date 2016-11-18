from base64 import b64decode
import common
import os
import re
import shutil
import subprocess
import tempfile
from six.moves.urllib.parse import urlsplit
import yaml

from charmhelpers.fetch import (
    apt_install,
    filter_installed_packages
)
from charmhelpers.core.hookenv import (
    charm_dir,
    config,
    log,
    relation_ids,
    relation_get,
    related_units,
    WARNING,
    INFO,
    ERROR
)
from cihelpers.gerrit import (
    GerritClient,
    start_gerrit,
    stop_gerrit
)
from cihelpers import cron

GERRIT_INIT_SCRIPT = '/etc/init.d/gerrit'
GERRIT_CONFIG_DIR = os.path.join(common.CI_CONFIG_DIR, 'gerrit')
THEME_DIR = os.path.join(GERRIT_CONFIG_DIR, 'theme')
HOOKS_DIR = os.path.join(GERRIT_CONFIG_DIR, 'hooks')
PERMISSIONS_DIR = os.path.join(GERRIT_CONFIG_DIR, 'permissions')
GROUPS_CONFIG_FILE = os.path.join(PERMISSIONS_DIR, 'groups.yml')
PROJECTS_CONFIG_FILE = os.path.join(GERRIT_CONFIG_DIR, 'projects',
                                    'projects.yml')
GIT_PATH = os.path.join('/srv', 'git')
SSH_PORT = 29418
GERRIT_USER = 'gerrit2'
GERRIT_HOME = os.path.join('/home', GERRIT_USER)
WAR_PATH = os.path.join(GERRIT_HOME, 'gerrit-wars', 'gerrit.war')
SITE_PATH = os.path.join(GERRIT_HOME, 'review_site')
LOGS_PATH = os.path.join(SITE_PATH, 'logs')
LAUNCHPAD_DIR = os.path.join(GERRIT_HOME, '.launchpadlib')
TEMPLATES = 'templates'
INITIAL_PERMISSIONS_COMMIT_MSG = "@ CI-CONFIGURATOR INITIAL PERMISSIONS SET @"


class GerritConfigurationException(Exception):
    pass


def update_theme(theme_dest, static_dest):
    if not os.path.isdir(THEME_DIR):
        log('Gerrit theme directory not found @ %s, skipping theme refresh.' %
            THEME_DIR, level=WARNING)
        return False

    theme_orig = os.path.join(THEME_DIR, 'files')
    static_orig = os.path.join(THEME_DIR, 'static')

    if False in [os.path.isdir(theme_orig), os.path.isdir(static_orig)]:
        log('Theme directory @ %s missing required subdirs: files, static. '
            'Skipping theme refresh.' % THEME_DIR, level=WARNING)
        return False

    log('Installing theme from %s to %s.' % (theme_orig, theme_dest))
    common.sync_dir(theme_orig, theme_dest)
    log('Installing static files from %s to %s.' % (theme_orig, theme_dest))
    common.sync_dir(static_orig, static_dest)

    return True


def update_hooks(hooks_dest, settings):
    if not os.path.isdir(HOOKS_DIR):
        log('Gerrit hooks directory not found @ %s, skipping hooks refresh.' %
            HOOKS_DIR, level=WARNING)
        return False

    log('Installing gerrit hooks in %s to %s.' % (HOOKS_DIR, hooks_dest))
    common.sync_dir(HOOKS_DIR, hooks_dest)

    #  hook allow tags like {{var}}, so replace all entries in file
    for filename in os.listdir(hooks_dest):
        current_path = os.path.join(hooks_dest, filename)
        if os.path.isfile(current_path):
            with open(current_path, 'r') as f:
                contents = f.read()
            for key, value in settings.items():
                pattern = '{{%s}}' % (key)
                contents = contents.replace(pattern, value)
            with open(current_path, 'w') as f:
                f.write(contents)

    return True


def setup_gerrit_groups(gerritperms_path, admin_username, admin_email):
    """Generate groups file"""
    cwd = os.getcwd()
    os.chdir(gerritperms_path)
    try:
        query = 'SELECT name, group_uuid FROM account_groups'
        cmd = ['java', '-jar', WAR_PATH, 'gsql', '-d', SITE_PATH, '-c', query]
        result = subprocess.check_output(cmd)
        if result:
            # parse file and generate groups
            output = result.splitlines()
            with open('groups', 'w') as f:
                for item in output[2:]:
                    # split between name and id
                    data = item.split('|')
                    if len(data) == 2:
                        group_cfg = ('%s\t%s\n' %
                                     (data[1].strip(), data[0].strip()))
                        f.write(group_cfg)

            cmds = [['git', 'config', '--global', 'user.name',
                     admin_username],
                    ['git', 'config', '--global', 'user.email',
                     admin_email],
                    ['git', 'commit', '-a', '-m', '"%s"' %
                     (INITIAL_PERMISSIONS_COMMIT_MSG)],
                    ['git', 'push', 'repo', 'meta/config:meta/config']]
            for cmd in cmds:
                common.run_as_user(user=GERRIT_USER, cmd=cmd,
                                   cwd=gerritperms_path)
        else:
            msg = 'Failed to query gerrit db for groups'
            raise GerritConfigurationException(msg)
    finally:
        os.chdir(cwd)


def is_permissions_initialised(repo_name, repo_path):
    """ The All-Projects.git repository is created by the Gerrit charm and
    configured by this charm. In order for it to be deemed initialised we need:

    1. expected branches
    2. initial permissions commit message
    """
    if repo_is_initialised("%s/%s" % (GIT_PATH, repo_name)):
        cmd = ['git', 'log', '--grep', INITIAL_PERMISSIONS_COMMIT_MSG]
        stdout = common.run_as_user(user=GERRIT_USER, cmd=cmd, cwd=repo_path)
        if stdout and INITIAL_PERMISSIONS_COMMIT_MSG in stdout:
            return True

        # NOTE(dosaboy): the ci-configurator used to set the same commit
        # message as the gerrit charm i.e. "Initial permissions" so if we don't
        # find the new-style message we then check for > 1 of the old-style
        # message.
        old_style_msg = "Initial permissions"
        cmd = ['git', 'log', '--grep', old_style_msg]
        stdout = common.run_as_user(user=GERRIT_USER, cmd=cmd, cwd=repo_path)
        if stdout:
            count = 0
            for line in stdout.split('\n'):
                if old_style_msg in line:
                    count += 1
                if count > 1:
                    return True

    return False


def update_permissions(admin_username, admin_email, admin_privkey):
    if not os.path.isdir(PERMISSIONS_DIR):
        log('Gerrit permissions directory not found @ %s, skipping '
            'permissions refresh.' % PERMISSIONS_DIR, level=WARNING)
        return False

    # create launchpad directory and setup permissions
    if not os.path.isdir(LAUNCHPAD_DIR):
        os.mkdir(LAUNCHPAD_DIR)
        cmd = ['chown', "%s:%s" % (GERRIT_USER, GERRIT_USER), LAUNCHPAD_DIR]
        subprocess.check_call(cmd)
        os.chmod(LAUNCHPAD_DIR, 0o774)

    # check if we have creds, push to dir
    if config('lp-credentials-file'):
        creds = b64decode(config('lp-credentials-file'))
        with open(os.path.join(LAUNCHPAD_DIR, 'creds'), 'w') as f:
            f.write(creds)

    # if we have teams and schedule, update cronjob
    if config('lp-schedule'):
        command = ('%s %s %s > %s 2>&1' %
                   (os.path.join(os.environ['CHARM_DIR'], 'scripts',
                    'query_lp_members.py'), admin_username, admin_privkey,
                    LOGS_PATH+'/launchpad_sync.log'))
        cron.schedule_generic_job(
            config('lp-schedule'), 'root', 'launchpad_sync', command)

    repo_name = 'All-Projects.git'
    repo_url = ('ssh://%s@localhost:%s/%s' % (admin_username, SSH_PORT,
                                              repo_name))

    # parse groups file and create groups
    gerrit_client = GerritClient(host='localhost', user=admin_username,
                                 port=SSH_PORT, key_file=admin_privkey)

    with open(GROUPS_CONFIG_FILE, 'r') as f:
        groups_config = yaml.load(f)

    # Create group(s)
    for group, _ in groups_config.items():
        gerrit_client.create_group(group)

    # Update git repo with permissions
    log('Installing gerrit permissions from %s.' % PERMISSIONS_DIR)
    try:
        tmppath = tempfile.mkdtemp('', 'gerritperms')
        if tmppath:
            cmd = ["chown", "%s:%s" % (GERRIT_USER, GERRIT_USER), tmppath]
            subprocess.check_call(cmd)
            os.chmod(tmppath, 0o774)

            config_ref = 'refs/meta/config:refs/remotes/origin/meta/config'

            for cmd in [['git', 'init'],
                        ['git', 'remote', 'add', 'repo', repo_url],
                        ['git', 'fetch', 'repo', config_ref],
                        ['git', 'checkout', 'meta/config']]:
                common.run_as_user(user=GERRIT_USER, cmd=cmd, cwd=tmppath)

            common.sync_dir(os.path.join(PERMISSIONS_DIR, 'All-Projects'),
                            tmppath)

            # Only proceed if the repo has NOT been successfully initialised.
            if is_permissions_initialised(repo_name, tmppath):
                log("%s is already initialised - skipping update permissions" %
                    (repo_name), level=INFO)
                return False

            try:
                setup_gerrit_groups(tmppath, admin_username, admin_email)
            except GerritConfigurationException as exc:
                log(str(exc), level=ERROR)
                return False
        else:
            log('Failed to create permissions temporary directory',
                level=ERROR)
            return False
    except Exception as e:
        log('Failed to create permissions: %s' % str(e), level=ERROR)

    return True


def setup_gitreview(path, repo, host):
    """
    Configure .gitreview so that when user clones repo the default git-review
    target is their CIaaS not upstream openstack.

    :param repo: <project>/<os-project>
    :param host: hostname/address of Gerrit git repository

    Returns list of commands to executed in the git repo to apply these
    changes.
    """
    cmds = []
    git_review_cfg = '.gitreview'
    target = os.path.join(path, git_review_cfg)

    log("Configuring %s" % (target))

    if not os.path.exists(target):
        log("%s not found in %s repo" % (target, repo), level=INFO)
        cmds.append(['git', 'add', git_review_cfg])

    # See https://bugs.launchpad.net/canonical-ci/+bug/1354923 for explanation
    # of why we are doing this here.
    try:
        import jinja2  # NOQA
    except ImportError:
        apt_install(filter_installed_packages(['python-jinja2']), fatal=True)
    finally:
        from jinja2 import Template

    templates_dir = os.path.join(charm_dir(), TEMPLATES)
    with open(os.path.join(templates_dir, git_review_cfg), 'r') as fd:
        t = Template(fd.read())
        rendered = t.render(repo=repo, host=host, port=SSH_PORT)

    with open(target, 'w') as fd:
        fd.write(rendered)

    msg = str("Configured git-review to point to '%s'" % (host))
    cmds.append(['git', 'commit', '-a', '-m', msg])

    return cmds


def repo_is_initialised(url, branches=None):
    """Query git repository to determine if initialised.

    Check id the common refs i.e. HEAD and refs/meta/config exist. If a list of
    branches is provided, they are checked as well.

    Returns True if all exist, otherwise returns False.

    :param branches: (optional) branches to check
    """
    # Get list of refs extant in the repo
    cmd = ['git', 'ls-remote', url]
    stdout = subprocess.check_output(cmd)

    # Match branches
    key = r"^[\S]+\s+?%s"

    # These two refs should always exist
    expected_refcount = 2
    keys = [re.compile(key % "HEAD")]
    keys.append(re.compile(key % "refs/meta/config"))

    if branches:
        expected_refcount += len(branches)
        keys += [re.compile(key % ("refs/heads/%s" % b)) for b in branches]

    found = 0
    for line in stdout.split('\n'):
        for i, key in enumerate(keys):
            result = key.match(line)
            if result:
                found += 1
                keys.pop(i)
                break

    if found == expected_refcount:
        return True

    return False


def get_gerrit_hostname(url):
    """Parse url to return just hostname part. Url may be IP address or FQDN.

    Returns hostname.
    """
    if not url:
        raise GerritConfigurationException("url is None")

    host = urlsplit(url).hostname
    if host:
        return host

    # If split does not yield a hostname, url is probably a hostname.
    return url


def create_projects(admin_username, admin_email, admin_privkey, base_url,
                    projects, branches, git_host, tmpdir):
    """Globally create all projects and repositories, clone and push"""
    cmd = ["chown", "%s:%s" % (GERRIT_USER, GERRIT_USER), tmpdir]
    subprocess.check_call(cmd)
    os.chmod(tmpdir, 0o774)

    gerrit_client = GerritClient(host='localhost', user=admin_username,
                                 port=SSH_PORT, key_file=admin_privkey)
    try:
        for project in projects:
            name, repo = project.values()

            if not gerrit_client.create_project(name):
                log("failed to create project in gerrit - skipping setup "
                    "for '%s'" % (name))
                continue

            git_srv_path = os.path.join(GIT_PATH, name)
            repo_path = os.path.join(tmpdir, name.replace('/', ''))
            repo_url = 'https://%s/%s' % (base_url, repo)
            gerrit_remote_url = "%s/%s.git" % (GIT_PATH, repo)

            # Only proceed if the repo has NOT been successfully initialised.
            if repo_is_initialised(gerrit_remote_url, branches):
                log("Repository '%s' already initialised - skipping" %
                    (git_srv_path), level=INFO)
                continue

            # Git config may not have been set yet so just in case.
            cmds = [['git', 'config', '--global', 'user.name', admin_username],
                    ['git', 'config', '--global', 'user.email', admin_email]]

            log("Cloning git repository '%s'" % (repo_url))
            cmds.append(['git', 'clone', repo_url, repo_path])
            for cmd in cmds:
                common.run_as_user(user=GERRIT_USER, cmd=cmd, cwd=tmpdir)

            # Setup the .gitreview file to point to this repo by default (as
            # opposed to upstream openstack).
            host = get_gerrit_hostname(git_host)
            cmds = setup_gitreview(repo_path, name, host)

            cmds.append(['git', 'remote', 'add', 'gerrit', gerrit_remote_url])
            cmds.append(['git', 'fetch', '--all'])

            for cmd in cmds:
                common.run_as_user(user=GERRIT_USER, cmd=cmd, cwd=repo_path)

            # Push to each branch if needed
            for branch in branches:
                branch = branch.strip()
                try:
                    cmd = ['git', 'show-branch', 'gerrit/%s' % (branch)]
                    common.run_as_user(user=GERRIT_USER, cmd=cmd,
                                       cwd=repo_path)
                except Exception:
                    # branch does not exist, create it
                    ref = 'HEAD:refs/heads/%s' % branch
                    cmds = [['git', 'checkout', branch],
                            ['git', 'pull'],
                            ['git', 'push', '--force', 'gerrit', ref]]
                    for cmd in cmds:
                        common.run_as_user(user=GERRIT_USER, cmd=cmd,
                                           cwd=repo_path)

            gerrit_client.flush_cache()
    except Exception as exc:
        msg = ('project setup failed (%s)' % str(exc))
        log(msg, ERROR)
        raise exc


def update_projects(admin_username, admin_email, privkey_path, git_host):
    """Install initial projects and branches based on config."""
    if not os.path.isfile(PROJECTS_CONFIG_FILE):
        log("Gerrit projects directory '%s' not found - skipping permissions "
            "refresh." % (PROJECTS_CONFIG_FILE), level=WARNING)
        return False

    # Parse yaml file to grab config
    with open(PROJECTS_CONFIG_FILE, 'r') as f:
        gerrit_cfg = yaml.load(f)

    for opt in ['base_url', 'branches', 'projects']:
        if opt not in gerrit_cfg:
            log("Required gerrit config '%s' not found in %s - skipping "
                "create_projects" % (opt, PROJECTS_CONFIG_FILE), level=WARNING)
            return False

    tmpdir = tempfile.mkdtemp()
    try:
        create_projects(admin_username, admin_email, privkey_path,
                        gerrit_cfg['base_url'], gerrit_cfg['projects'],
                        gerrit_cfg['branches'], git_host, tmpdir)
    finally:
        # Always cleanup
        shutil.rmtree(tmpdir)

    return True


def get_relation_settings(keys):
    """Fetch required relation settings.

    If any setting is unset ('' or None) we return None.

    :param keys: Setting keys to look for.
    """
    settings = {}
    try:
        for rid in relation_ids('gerrit-configurator'):
            for unit in related_units(rid):
                for key in keys:
                    settings[key] = relation_get(key, rid=rid, unit=unit)

    except Exception as exc:
        log('Failed to get gerrit relation data (%s).' % (exc), level=WARNING)
        return

    missing = [k for k, v in settings.items() if not v]
    if missing:
        log("Missing value for '%s' in gerrit relation." %
            (','.join(missing)), level=WARNING)
        return

    return settings


def update_gerrit():
    if not relation_ids('gerrit-configurator'):
        log('*** No relation to gerrit, skipping update.')
        return

    log("*** Updating gerrit.")
    if not os.path.isdir(GERRIT_CONFIG_DIR):
        log('Could not find gerrit config directory at expected location, '
            'skipping gerrit update (%s)' % GERRIT_CONFIG_DIR, level=WARNING)
        return

    required_keys = ['admin_username', 'admin_email', 'admin_privkey_path',
                     'review_site_dir', 'git_host']
    rel_settings = get_relation_settings(required_keys)
    if not rel_settings:
        log("Missing or invalid relation settings - skipping update",
            level=INFO)
        return

    review_site_dir = rel_settings['review_site_dir']
    admin_username = rel_settings['admin_username']
    admin_email = rel_settings['admin_email']
    admin_privkey_path = rel_settings['admin_privkey_path']
    git_host = rel_settings['git_host']

    # Any of the following operations may require a restart.
    restart_req = []

    restart_req.append(update_projects(admin_username, admin_email,
                                       admin_privkey_path, git_host))

    restart_req.append(update_permissions(admin_username, admin_email,
                                          admin_privkey_path))

    # Installation location of hooks and theme, based on review_site path
    # exported from principle
    hooks_dir = os.path.join(review_site_dir, 'hooks')
    theme_dir = os.path.join(review_site_dir, 'etc')
    static_dir = os.path.join(review_site_dir, 'static')

    restart_req.append(update_hooks(hooks_dir, rel_settings))
    restart_req.append(update_theme(theme_dir, static_dir))

    if any(restart_req):
        stop_gerrit()
        start_gerrit()
