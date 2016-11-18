import os

from charmhelpers.core.hookenv import log, INFO


def write_cronjob(content, job_name=''):
    f = os.environ["JUJU_UNIT_NAME"].replace("/", "_")
    cron_path = os.path.join('/etc', 'cron.d', f)
    if job_name:
        cron_path+='_'+job_name

    f = open(cron_path, "w")
    f.write(content)
    f.close()
    os.chmod(cron_path, 0o755)
    log("Wrote cronjob to %s." % cron_path, INFO)


# generic backup job creation
def schedule_backup(sources, ci_user, target, schedule, retention_count):
    log("Creating backup cronjob for sources: %s." % sources, INFO)

    # if doesn't exist, create backup directory and scripts directory
    if not os.path.exists(target):
        os.makedirs(target)
        os.chmod(target, 0o755)

    script = os.path.join(os.environ['CHARM_DIR'],
                          "scripts/backup_job")
    backup_string = ",".join(sources)

    # create the cronjob file that will call the script
    content = ("%s %s %s %s %s %s\n" %
               (schedule, ci_user, script, backup_string, target, retention_count))
    write_cronjob(content)


def schedule_repo_updates(schedule, ci_user, ci_config_dir, conf_repo_rcs,
                          jobs_config_dir):
    log("Creating cronjob to update CI repo config.", INFO)

    update_command = \
        "/bin/sh -c 'cd %s && %s pull && jenkins-job-builder update %s'" % \
        (ci_config_dir, conf_repo_rcs, jobs_config_dir)

    content = "%s %s %s\n" % (schedule, ci_user, update_command)
    write_cronjob(content)


def schedule_generic_job(schedule, user, name, job):
    content = "%s %s %s\n" % (schedule, user, job)
    write_cronjob(content, job_name=name)
