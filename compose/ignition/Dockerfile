# Ignition 8.3 with the Cirrus Link MQTT modules baked in.
#
# Why an image and not a bind mount
# ---------------------------------
# The 8.3 image sets
#     -Dignition.gateway.externalModulesFolder=data/var/ignition/modl
# and discovers .modl files there only on the FIRST launch of a fresh data
# volume. (The /modules bind-mount convention and GATEWAY_MODULE_RELINK are
# 8.1-era; the 8.3 entrypoint handles neither.)
#
# That folder lives inside data/, which is a named volume, and Docker seeds a
# named volume from the image only when the volume is EMPTY. Bind-mounting into
# any subpath of it makes it non-empty before seeding, so data/config and
# data/projects never get created and the gateway comes up with no
# configuration at all. Baking the modules into the image avoids that: they ride
# along with the normal volume seeding.
#
# Do NOT create a data_clean/ directory here either. The entrypoint treats its
# existence as "the real payload lives here", copies only its contents into
# data/, then deletes it -- silently replacing the real config with your staging
# directory.
#
# Consequence for the team: adding or upgrading a module means
#     python tasks.py nuke     (an existing volume is never re-seeded)
#     python tasks.py seed
# Rebuilding the image on its own is not enough.
#
# Note on commissioning: the presence of ANY third-party module makes the
# gateway require a one-time interactive acceptance of its certificate before it
# will finish starting. That cannot be pre-empted by pre-seeding modules.json
# with certificate fingerprints -- tested, it does not work. `tasks.py seed`
# walks you through it once per fresh volume.

ARG IGNITION_VERSION=8.3.8
FROM inductiveautomation/ignition:${IGNITION_VERSION}

# Base image runs as 2003:2003; root is needed to write into the install tree.
USER root

COPY modules/ /usr/local/bin/ignition/data/var/ignition/modl/

# .gitkeep exists only so git tracks an otherwise-empty directory; Ignition
# should not see it. The build stays tolerant of no .modl files being present --
# the gateway then comes up without MQTT, a clearer failure than a broken build.
RUN rm -f /usr/local/bin/ignition/data/var/ignition/modl/.gitkeep \
 && chown -R 2003:2003 /usr/local/bin/ignition/data/var

USER 2003:2003
