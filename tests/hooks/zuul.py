import os
import subprocess

import common

from charmhelpers.core.hookenv import log, related_units, relation_ids, INFO

ZUUL_CONFIG_DIR = os.path.join(common.CI_CONFIG_DIR, 'zuul')
ZUUL_INIT_SCRIPT = "/etc/init.d/zuul"


# start and stop services
def start_zuul():
    log("*** Starting zuul server ***", INFO)
    try:
        subprocess.call([ZUUL_INIT_SCRIPT, "start"])
    except:
        pass


def stop_zuul():
    log("*** Stopping zuul server ***", INFO)
    try:
        subprocess.call([ZUUL_INIT_SCRIPT, "stop"])
    except:
        pass


def update_zuul():
    zuul_units = []

    for rid in relation_ids('zuul-configurator'):
        [zuul_units.append(u) for u in related_units(rid)]

    if not zuul_units:
        log('*** No related zuul units, skipping config.')
        return

    log("*** Updating zuul.")
    layout_path = '/etc/zuul/layout.yaml'

    if not os.path.isdir(ZUUL_CONFIG_DIR):
        log('Could not find zuul config directory at expected location, '
            'skipping zuul update (%s)' % ZUUL_CONFIG_DIR)
        return

    log('Installing layout from %s to %s.' % (ZUUL_CONFIG_DIR, layout_path))
    common.sync_dir(ZUUL_CONFIG_DIR, layout_path)

    stop_zuul()
    start_zuul()

    return True
