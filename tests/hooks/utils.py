import os

from charmhelpers.core.hookenv import log

import common


def is_valid_config_repo(conf_repo_rcs, location):
    if not location:
        return False
    if conf_repo_rcs == 'bzr':
        if location.startswith('lp:') or location.startswith('bzr'):
            return True
    elif conf_repo_rcs == 'git':
        if location.startswith('lp:'):
            return False
        # git supports "user@host:...", so validation would be very complex
        return True
    else:
        log('Unknown config-repo-rcs: {}'.format(conf_repo_rcs))

    return False


def is_ci_configured():
    return os.path.isdir(common.CI_CONFIG_DIR)
