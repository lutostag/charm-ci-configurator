#!/bin/sh
''''. /etc/os-release; dpkg --compare-versions $VERSION_ID ge "16.04" && exec /usr/bin/env python3 "$0" "$@" # '''
''''which python2 >/dev/null 2>&1 && exec /usr/bin/env python2 "$0" "$@" # '''
''''exec echo "Error: I can't find python anywhere" # '''

import os
import sys
import shlex
import shutil
import subprocess

import gerrit
import jjb
import zuul

from utils import (
    is_ci_configured,
    is_valid_config_repo,
)

from charmhelpers.fetch import apt_install, filter_installed_packages
from cihelpers import cron
import common
from charmhelpers.core.hookenv import (
    charm_dir,
    config,
    log,
    DEBUG,
    INFO,
    relation_ids,
    related_units,
    relation_get,
    relation_set,
    Hooks,
    UnregisteredHookError,
    local_unit
)
from charmhelpers.core.host import mkdir
from charmhelpers.core.templating import render

hooks = Hooks()


@hooks.hook()
def install():
    common.ensure_user()
    if not os.path.exists(common.CONFIG_DIR):
        os.mkdir(common.CONFIG_DIR)
    apt_install(filter_installed_packages(common.PACKAGES), fatal=True)


def run_relation_hooks():
    """Run relation hooks (if relations exist) to ensure that configs are
    updated/accurate.
    """
    for rid in relation_ids('jenkins-configurator'):
        if related_units(relid=rid):
            log("Running jenkins-configurator-changed hook", level=DEBUG)
            jenkins_configurator_relation_changed(rid=rid)

    for rid in relation_ids('gerrit-configurator'):
        if related_units(relid=rid):
            log("Running gerrit-configurator-changed hook", level=DEBUG)
            gerrit_configurator_relation_changed(rid=rid)

    for rid in relation_ids('zuul-configurator'):
        if related_units(relid=rid):
            log("Running zuul-configurator-changed hook", level=DEBUG)
            zuul_configurator_relation_changed(rid=rid)


@hooks.hook()
def config_changed():
    # setup identity to reach private LP resources
    common.ensure_user()
    common.install_ssh_keys() 
    lp_user = config('lp-login')
    if lp_user:
        cmd = ['bzr', 'launchpad-login', lp_user]
        common.run_as_user(cmd=cmd, user=common.CI_USER)

    # NOTE: this will overwrite existing configs so relation hooks will have to
    # re-run in order for settings to be re-applied.
    bundled_repo = os.path.join(charm_dir(), common.LOCAL_CONFIG_REPO)
    conf_repo = config('config-repo')
    conf_repo_rcs = config('config-repo-rcs')
    if os.path.exists(bundled_repo) and os.path.isdir(bundled_repo):
        common.update_configs_from_charm(bundled_repo)
        run_relation_hooks()
    elif is_valid_config_repo(conf_repo_rcs, conf_repo):
        common.update_configs_from_repo(conf_repo_rcs,
                                        conf_repo,
                                        config('config-repo-revision'))
        run_relation_hooks()

    if config('schedule-updates'):
        schedule = config('update-frequency')
        cron.schedule_repo_updates(
            schedule, common.CI_USER, common.CI_CONFIG_DIR, conf_repo_rcs,
            jjb.JOBS_CONFIG_DIR)


@hooks.hook()
def upgrade_charm():
    config_changed()


@hooks.hook()
def jenkins_configurator_relation_joined(rid=None):
    """Install jenkins job builder.

    Also inform jenkins of any plugins our tests may require, as defined in
    the control.yml of the config repo.
    """
    jjb.install()
    plugins = jjb.required_plugins()
    if plugins:
        relation_set(relation_id=rid, required_plugins=' '.join(plugins))


@hooks.hook('jenkins-configurator-relation-changed')
def jenkins_configurator_relation_changed(rid=None):
    """Update/configure Jenkins installation.

    Also ensures that JJB and any required plugins are installed.
    """
    # Ensure jjb and any available plugins are installed before attempting
    # update.
    jenkins_configurator_relation_joined(rid=rid)

    if is_ci_configured():
        if os.path.isdir(jjb.CONFIG_DIR):
            jjb.update_jenkins()
        else:
            log("jjb not installed - skipping update", level=INFO)
    else:
        log('CI not yet configured - skipping jenkins update', level=INFO)


@hooks.hook('gerrit-configurator-relation-changed')
def gerrit_configurator_relation_changed(rid=None):
    """Update/configure Gerrit installation."""
    if is_ci_configured():
        gerrit.update_gerrit()
    else:
        log('CI not yet configured - skipping gerrit update', level=INFO)


@hooks.hook('zuul-configurator-relation-changed')
def zuul_configurator_relation_changed(rid=None):
    """Update/configure Zuul installation."""
    if is_ci_configured():
        zuul.update_zuul()
    else:
        log('CI not yet configured - skipping zuul update', level=INFO)


@hooks.hook('vault-relation-changed')
def vault_relation_changed(rid=None):
    content = {'host': None, 'port': None, 'token': None}
    for rid in relation_ids('vault'):
        for unit in related_units(rid):
            for key in content:
                value = relation_get(key, rid=rid, unit=unit)
                if value:
                    content[key] = value
    if not all(content.values()):
        return
    shutil.copyfile(
        '{}/files/vault-0.5.0'.format(charm_dir()),
        '/tmp/vault')
    mkdir('/usr/local/bin')
    shutil.move('/tmp/vault', '/usr/local/bin/vault')
    os.chmod('/usr/local/bin/vault', 0o755)

    render('vault-client', '/usr/local/bin/vault-client', content, perms=0o755)
    user = 'jenkins'
    cmd = "/bin/su -c '/usr/local/bin/vault-client auth %s' %s" % \
        (content['token'], user)
    subprocess.check_call(shlex.split(cmd))


@hooks.hook('vault-relation-joined')
def vault_relation_joined(rid=None):
    relation_set(relation_id=rid, token=local_unit())


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))


if __name__ == '__main__':
    main()
