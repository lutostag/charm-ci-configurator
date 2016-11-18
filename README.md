Overview
========

This charm provides the ability to manage configuration of Juju-deployed
CI infrastructure, including the Jenkins, Zuul and Gerrit services.  It
is intended to manage the three of them together to create a CI
review/build/test pipeline, but can be used to a single service or subset of
services if the entire pipeline is not required.

The charm functions as a subordinate charm with separate interfaces capable of
relating to the three principle services (Jenkins, Zuul and Gerrit).  The
charm contains minimal Juju charm configuration data and instead makes use
of a specified bzr repository to allow users to manage configuration out of
band from the Juju environment.  Doing so allows users other than those
with direct access to the Juju environment to push configuration changes
to the principle services.

Usage
=====

The deployment of Jenkins, Zuul and Gerrit services are beyond the scope
of this document.  Assuming they have been deployed and properly related,
this subordinate may be deployed with proper configuration settings and
related to each.  It may be related to each principle service by via
its own relation interface: gerrit-configurator, jenkins-configurator
and zuul-configurator.  Relation hooks for each interface know how to
configure the principle service based on data contained in the configured
bzr repository.

    juju deploy ci-configurator
    juju add-relation ci-configurator zuul
    juju add-relation ci-configurator gerrit
    juju add-relation ci-configurator jenkins
    juju add-relation ci-configurator jenkins-slave

Repository management, offline and online
=========================================

This charm is intended to be deployed in environments that have outbound
internet access.  Internet access is used for branching the configured
bzr repository and installing the jenkins-job-builder project from pypi.

In environments where internet access is restricted, both of these may
be "bundled" with the charm prior to deploying, such that they will be
shipped to machine units in and installed from the charm itself.  To
do so, you must branch the charm locally and run two Makefile targets:

    $ bzr branch lp:~canonical-ci/charms/precise/ci-configurator/trunk \
            ci-configurator
    $ cd ci-configurator

    # Pulls in jenkins-job-builder and required dependencies
    $ make sourcedeps

    # Branches specified repository and bundles it in charm.  This branch
    # is what would be configured as 'config-repo' configuration setting
    # in an online environment.
    $ CONFIG_BZR_REPO=lp:~canonical-ci/canonical-ci/<project>-ci-config \
       make configrepo

You may now deploy from the local repository and resources will be available to
the charm locally instead of attempting to fetch from the net.

In either case, the repository ends up cloned to:

    /etc/ci-confiurator/ci-config-repo.


Repository Format
=================

The bzr repository that is branched and used by the charm should follow a
specified layout, as the charm expects certain files and scripts to be in
specific locations.  At the top level, a subdirectory should exist for
each service the repository is managing.  If one is missing and a relation
is added, configuration of that service is skipped:

    $ tree -L 1.
    |--control.yml
    +-- gerrit
    +-- jenkins
    +-- zuul

The control.yml file is currently used to specify additional package
and plugin dependencies that are required to run the jenkins jobs configured
in the repository (see jenkins section below), but the scope of this file may
expand in the future to also specify dependencies for Zuul and Gerrit.

The layout of the per-service subdirectories and the integration with the charm
is described below:

gerrit/
------

The ci-configurator charm knows how to manage gerrit hooks, permissions,
projects and the gerrit theme.  Each bit is managed in its own subdirectory:

    gerrit/
    |-- hooks
    |   |-- change-merged
    +-- permissions
    |   |-- All-Projects
    |       |-- project.config
    +-- projects
    |       |-- projects.yml
    +-- theme
        +-- | files
        |   |-- GerritSite.css
        |   |-- GerritSiteHeader.html
        +-- static
            |-- canonical_header_b
            |-- canonical_logo

Files in hooks/ get installed into the local gerrit services hooks directory
(/home/gerrit2/review_site/hooks/ by default)

The projects.yml file in projects/ subdirectory contains the Gerrit projects
configuration.  The permissions/All-Projects/project.config contains the
permissions settings for all configured Projects. When updating, these files
get committed and pushed to the local, internal gerrit config git repository.

The files hosted in the theme/ directory are used to customize the gerrit
theme and get installed to /home/gerrit2/review_site/etc/ and
/home/gerrit2/review_site/static/ by default.

If the gerrit/ subdirectory is missing in repository, updating gerrit is
skipped.  If any subdirectory of gerrit/ is missing in repository,
configuration of that gerrit component is skipped.

    required_jenkins_packages:
        - git
        - python-pip
        - python-mock
    required_jenkins_plugins:
        - git-client
        - git

zuul
----

The zuul section of the repository currently is used only for installing
the zuul layout.yml:

    zuul/
    -- layout.yml

When updating zuul, this file is installed to /etc/zuul/layout.yaml and the
service restarted.

jenkins
-------

The charm makes use of the jenkins-job-builder tool to define jenkins
jobs and update jenkins server configuration:

    https://github.com/openstack-infra/jenkins-job-builder

The repository should contain jenkins-job-builder compatable yaml job
templates in jenkins/jobs/ subdirectory.  It should also contain a
executable script named 'update' which is used by the charm for injecting
any relevant environment data into the yaml templates where required:

    jenkins/
    |-- jobs/
    |---- job_templates.yml
    |---- macros.yml
    |---- projects.yml
    |---- update
    |-- security/
    |---- config.xml

Its currently up to the repository's update script to decide how it wants
to inject data into the jenkins-job-builder configs.  Prior to calling this
update script, the charm's configuration and relation data to principle
jenkins service is dumped to a json file at:

    /etc/ci-configurator/charm_context.json

One example use case:

The jenkins_job configs setup jenkins jobs that use the gearman plugin
to connect to a remote zuul service (expressed by a
jenkins <-> zuul relation).  The principle jenkins service exports the zuul
address via its relation to the subordinate ci-configurator service.  The
ci-configurator charm dumps this address and other charm context to 
the json file and calls the repo's update hook.  The update hook then
injects the zuul address into the various job configurations.

After the update hook is called, the 'jenkins-jobs' script is called to
actually create or update the jobs in the running jenkins cluster.

It may also be required to install additional packages or Jenkins plugins
in order to run the jobs defined in the repository.  These may be defined
in the control.yml file shipped in the reposotiry.  This packages and 
plugins will be installed on the jenkins principle upon every hook run:

    required_jenkins_packages:
        - git
        - python-pip
        - python-mock
    required_jenkins_plugins:
        - ssh-agent
        - gearman-plugin
        - git-client
        - git


Updating configuration
======================

Updating configuration in any of the services is a matter of updating
the config repository and triggering a charm event that will pull in
the changes and run the various update bits.  The charm has the ability
to run from a specified revision of the repository, or to run from trunk.
Triggering an update depends on whether the charm was deployed in offline
mode or online mode and whether it is set to run from a revision or trunk.

Online
------

If set to run from trunk, the charm may optionally install a cronjob to 
pull in new revisions on a specified interval and update configuation
in services. Optionally, if you do not wish to have this automated you
may trigger updates using via the update-trigger config setting:

    juju set ci-configurator update-trigger=$(uuid)

If set to run from a specified revision, it is just a matter of setting
the revision in the charm config.  The charm will pull the revision and run
the various update bits.

(NOTE: cron installation still TODO)

Offline
-------

New revisions to the repository need to be rebundled locally into the charm,
the local charms revision bumped and the charm upgrade.  If the repository
bundled with the charm already contains an updated repository, and you wish
to run from a specified revision number, this may be set normally via
'juju set'
