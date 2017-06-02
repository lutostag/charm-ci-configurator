import json
import os
import shutil
import subprocess
from six.moves.urllib.error import HTTPError
import time
import xml.etree.ElementTree as ET

import common

from charmhelpers.core.hookenv import (
    charm_dir, config, log, relation_ids, relation_get,
    related_units, ERROR)
from charmhelpers.fetch import (
    apt_install, apt_update, filter_installed_packages)
from charmhelpers.core.host import lsb_release, restart_on_change

PACKAGES = ['git', 'python-pip']
CONFIG_DIR = '/etc/jenkins_jobs'
JJB_CONFIG = os.path.join(CONFIG_DIR, 'jenkins_jobs.ini')

JENKINS_CONFIG_DIR = os.path.join(common.CI_CONFIG_DIR, 'jenkins')
JOBS_CONFIG_DIR = os.path.join(JENKINS_CONFIG_DIR, 'jobs')
CHARM_CONTEXT_DUMP = os.path.join(common.CI_CONFIG_DIR, 'charm_context.json')

JENKINS_SECURITY_FILE = os.path.join(JENKINS_CONFIG_DIR,
                                     'security', 'config.xml')
JENKINS_PATH = '/var/lib/jenkins'
JENKINS_CONFIG_FILE = '/var/lib/jenkins/config.xml'

# locaiton of various assets Makefile target creates.
TARBALL = 'jenkins-job-builder.tar.gz'
LOCAL_PIP_DEPS = 'jenkins-job-builder_reqs'
LOCAL_JOBS_CONFIG = 'job-configs'
SLEEP_TIME = 30
MAX_RETRIES = 10

JJB_CONFIG_TEMPLATE = """
[jenkins]
user=%(username)s
password=%(password)s
url=%(jenkins_url)s
"""


def install():
    """
    Install jenkins-job-builder from a archive, remote git repository or a
    locally bundled copy shipped with the charm.  Any locally bundled copy
    overrides 'jjb-install-source' setting.
    """
    if not os.path.isdir(CONFIG_DIR):
        os.mkdir(CONFIG_DIR)
    src = config('jjb-install-source')
    tarball = os.path.join(charm_dir(), 'files', TARBALL)

    if os.path.isfile(tarball):
        log('Installing jenkins-job-builder from bundled file: %s.' % tarball)
        install_from_file(tarball)
    elif src.startswith('git://') or src.startswith('https://'):
        log('Installing jenkins-job-builder from remote git: %s.' % src)
        install_from_git(src)
    elif src == 'distro':
        log('Installing jenkins-job-builder from Ubuntu archive.')
        if lsb_release()['DISTRIB_CODENAME'] in ['precise', 'quantal']:
            m = ('jenkins-job-builder package only available in Ubuntu 13.04 '
                 'and later.')
            raise Exception(m)
        apt_update(fatal=True)
        apt_install(['jenkins-job-builder', 'python-pbr'],
                    fatal=True)
    else:
        m = ('Must specify a git url as install source or bundled source with '
             'the charm.')
        log(m, ERROR)
        raise Exception(m)


def _clean_tmp_dir(tmpdir):
    tmpdir = os.path.join('/tmp', 'jenkins-job-builder')
    if os.path.exists(tmpdir):
        if os.path.isfile(tmpdir):
            os.unlink(tmpdir)
        else:
            shutil.rmtree(tmpdir)


def install_from_file(tarball):
    log('*** Installing from local tarball: %s.' % tarball)
    outdir = os.path.join('/tmp', 'jenkins-job-builder')
    _clean_tmp_dir(outdir)

    apt_install(filter_installed_packages(['python-pip']), fatal=True)
    os.chdir(os.path.dirname(outdir))
    cmd = ['tar', 'xfz', tarball]
    subprocess.check_call(cmd)
    os.chdir(outdir)
    deps = os.path.join(charm_dir(), 'files', LOCAL_PIP_DEPS)
    cmd = ['pip', 'install', '--no-index',
           '--find-links=file://%s' % deps, '-r', 'requirements.txt']
    subprocess.check_call(cmd)
    cmd = ['python', './setup.py', 'install']
    subprocess.check_call(cmd)
    log('*** Installed from local tarball.')


def install_from_git(repo):
    # assumes internet access
    log('*** Installing from remote git repository: %s' % repo)
    apt_install(filter_installed_packages(['git', 'python-pip']), fatal=True)
    cmd = ['pip', 'install', 'git+{}'.format(repo)]
    subprocess.check_call(cmd)


def write_jjb_config():
    log('*** Writing jenkins-job-builder config: %s.' % JJB_CONFIG)
    jenkins = {}
    admin_user, admin_cred = admin_credentials()
    for rid in relation_ids('jenkins-configurator'):
        for unit in related_units(rid):
            jenkins = {
                'jenkins_url': relation_get('jenkins_url', rid=rid, unit=unit),
                'username': admin_user,
                'password': admin_cred,
            }

            if (None not in jenkins.values() and
                    '' not in jenkins.values()):
                with open(JJB_CONFIG, 'w') as out:
                    out.write(JJB_CONFIG_TEMPLATE % jenkins)
                log('*** Wrote jenkins-job-builder config: %s.' % JJB_CONFIG)
                return True

    log('*** Not enough data in principle relation. Not writing config.')
    return False


def jenkins_context():
    for rid in relation_ids('jenkins-configurator'):
        for unit in related_units(rid):
            return relation_get(rid=rid, unit=unit)


def config_context():
    ctxt = {}
    for k, v in config().items():
        if k == 'misc-config':
            _misc = v.split(' ')
            for ms in _misc:
                if '=' in ms:
                    x, y = ms.split('=')
                    ctxt.update({x: y})
        else:
            ctxt.update({k: v})
    return ctxt


def save_context(outfile=CHARM_CONTEXT_DUMP):
    '''dumps principle relation context and config to a json file for
    use by jenkins-job-builder repo update hook'''
    log('Saving current charm context to %s.' % CHARM_CONTEXT_DUMP)
    ctxt = {}
    ctxt.update(jenkins_context())
    ctxt.update(config_context())
    with open(CHARM_CONTEXT_DUMP, 'w') as out:
        out.write(json.dumps(ctxt))


def admin_credentials():
    """fetches admin credentials either from charm config or remote jenkins
    service"""

    for rid in relation_ids('jenkins-configurator'):
        admin_user = None
        admin_cred = None
        for unit in related_units(rid):
            jenkins_admin_user = relation_get('jenkins-admin-user',
                                              rid=rid, unit=unit)
            jenkins_token = relation_get('jenkins-token',
                                         rid=rid, unit=unit)
            if (jenkins_admin_user and jenkins_token) and '' not in \
               [jenkins_admin_user, jenkins_token]:
                log(('Configurating Jenkins credentials '
                     'from charm configuration.'))
                return jenkins_admin_user, jenkins_token

            admin_user = relation_get('admin_username', rid=rid, unit=unit)
            admin_cred = relation_get('admin_password', rid=rid, unit=unit)
            if (admin_user and admin_cred) and \
               '' not in [admin_user, admin_cred]:
                log('Configuring Jenkins credentials from Jenkins relation.')
                return (admin_user, admin_cred)

    return (None, None)


def is_jenkins_slave():
    return os.path.isfile('/etc/init/jenkins-slave.conf')


def _update_jenkins_config():
    if not os.path.isdir(JOBS_CONFIG_DIR):
        log('Could not find jobs-config directory at expected location, '
            'skipping jenkins-jobs update (%s)' % JOBS_CONFIG_DIR, ERROR)
        return

    log('Updating jenkins config @ %s' % JENKINS_CONFIG_FILE)
    if not os.path.isfile(JENKINS_SECURITY_FILE):
        log('Could not find jenkins config file @ %s, skipping.' %
            JENKINS_SECURITY_FILE)
        return

    # open existing config.xml and manipulate to enable
    # our security rules, use parser to don't overwrite it
    tree = ET.parse(JENKINS_CONFIG_FILE)
    securityItem = tree.find('useSecurity')
    if securityItem is not None:
        securityItem.text = 'True'
    else:
        # create security item
        root = tree.getroot()
        parent = root.find(".")
        securityItem = ET.SubElement(parent, 'useSecurity')
        securityItem.text = 'True'

    # now replace authorization strategy with our bits
    root = tree.getroot()
    auth = root.find('authorizationStrategy')
    if auth is not None:
        root.remove(auth)

    # create our own tree with security bits
    secElement = ET.parse(JENKINS_SECURITY_FILE)
    root.append(secElement.getroot())

    tree.write(JENKINS_CONFIG_FILE)
    cmd = ['chown', 'jenkins:nogroup', JENKINS_CONFIG_FILE]
    subprocess.check_call(cmd)
    os.chmod(JENKINS_CONFIG_FILE, 0o644)

    # restart only if needed
    restart_on_change({JENKINS_CONFIG_FILE: ['jenkins']})


def _get_jjb_cmd():
    possible_commands = ['jenkins-job-builder', 'jenkins-jobs']
    possible_commands = ['which %s 2>/dev/null' % command
                         for command in possible_commands]
    try:
        return subprocess.check_output(' || '.join(possible_commands),
                                       shell=True).strip()
    except:
        log('Could not find any jenkins-job command', ERROR)
        raise


def _update_jenkins_jobs():
    if not write_jjb_config():
        log('Could not write jenkins-job-builder config, skipping '
            'jobs update.')
        return
    if not os.path.isdir(JOBS_CONFIG_DIR):
        log('Could not find jobs-config directory at expected location, '
            'skipping jenkins-jobs update (%s)' % JOBS_CONFIG_DIR, ERROR)
        return

    save_context()
    # inform hook where to find the context json dump
    os.environ['JJB_CHARM_CONTEXT'] = CHARM_CONTEXT_DUMP
    os.environ['JJB_JOBS_CONFIG_DIR'] = JOBS_CONFIG_DIR

    hook = os.path.join(JOBS_CONFIG_DIR, 'update')
    if not os.path.isfile(hook):
        log('Could not find jobs-config update hook at expected location: ' +
            "%s, continuing anyways." % hook, ERROR)
    else:
        log('Calling jenkins-job-builder repo update hook: %s.' % hook)
        subprocess.check_call(hook)

    # call jenkins-jobs to actually update jenkins
    # TODO: Call 'jenkins-job test' to validate configs before updating?
    log('Updating jobs in jenkins.')

    # call jenkins-jobs update, wait for jenkins to be available if needed
    # because it comes after a restart, so needs time
    for attempt in range(MAX_RETRIES):
        try:
            cmd = [_get_jjb_cmd(), 'update', JOBS_CONFIG_DIR]
            # Run as the CI_USER so the cache will be primed with the correct
            # permissions (rather than root:root).
            common.run_as_user(cmd=cmd, user=common.CI_USER)
        except HTTPError as err:
            if err.code == 503:
                # sleep for a while, retry
                time.sleep(SLEEP_TIME)
                log('Jenkins is still not available, retrying')
                continue
            else:
                log('Error updating jobs, check jjb settings and retry', ERROR)
        except Exception as e:
            log('Error updating jobs, check jjb settings and retry: %s' %
                str(e), ERROR)
            raise
        break


def update_jenkins():
    if not relation_ids('jenkins-configurator'):
        return

    # if jenkins lib does not exist, skip it
    if not os.path.isdir(JENKINS_PATH):
        log(('*** Jenkins does not exist. Not in jenkins relation, '
             'skipping ***'))
        return

    # install any packages that the repo says we need as dependencies.
    pkgs = required_packages()
    if pkgs:
        opts = []
        if config('force-package-install'):
            opts = [
                '--option', 'Dpkg::Options::=--force-confnew',
                '--option', 'Dpkg::Options::=--force-confdef',
            ]
        apt_install(pkgs, options=opts, fatal=True)

    log("*** Updating jenkins.")
    if not is_jenkins_slave():
        log('Running on master, updating config and jobs.')
        _update_jenkins_config()
        _update_jenkins_jobs()

    # run repo setup scripts.
    setupd = os.path.join(common.CI_CONFIG_DIR, 'setup.d')
    if os.path.isdir(setupd):
        cmd = ["run-parts", "--exit-on-error", setupd]
        log('Running repo setup.')
        subprocess.check_call(cmd)


def required_packages():
    control = common.load_control()
    if control and 'required_jenkins_packages' in control:
        return control['required_jenkins_packages']


def required_plugins():
    control = common.load_control()
    if control and 'required_jenkins_plugins' in control:
        return control['required_jenkins_plugins']
