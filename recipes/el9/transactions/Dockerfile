# Scenario base image for the EL9 upgrade-transaction matrix.
#
# This is stock almalinux:9 with everything EXCEPT our packages already present:
# the distribution fully updated, EPEL enabled (the cohort runtime needs
# libunwind.so.8, which is not in BaseOS or AppStream), the cohort's OS-level
# runtime dependencies installed, and the versionlock plugin available.
#
# Why pre-install all that rather than let each scenario pull it:
#
#   * every scenario gets a byte-identical starting state, so a difference in
#     dnf's output is a difference in the transaction, not in what the mirror
#     happened to serve that minute;
#   * `dnf upgrade` on a half-updated container proposes a hundred unrelated
#     BaseOS updates, and the one line about vinyl-cache is lost in it. With the
#     image already current, the transaction output is about the cohort;
#   * it keeps the scenarios offline-equivalent: the only repositories with
#     anything to offer are our two local ones.
#
# Each scenario still runs in a FRESH container from this image. Nothing carries
# over between scenarios.

FROM almalinux:9

RUN dnf -y install epel-release \
 && dnf -y upgrade \
 && dnf -y --setopt=install_weak_deps=False install \
      gcc logrotate redhat-rpm-config shadow-utils systemd \
      libunwind pcre2 libedit ncurses-libs \
      python3 python3-dnf-plugin-versionlock \
      createrepo_c diffutils findutils \
 && dnf clean all

# Anything installed after this point is the transaction under test.
