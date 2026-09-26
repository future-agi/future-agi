# Sourced by the RUN steps of futureagi/Dockerfile.oss that depend on the
# image variant (build time only). IMAGE_VARIANT sets the default of every
# per-feature build arg; a per-feature arg passed with a value wins. Exports
# the resolved values and fails the build on an unknown one.
#
#   standard (default)  feature-complete: the dependency set, uv, git,
#            ffmpeg, NLTK data and untrimmed site-packages of every release
#            before the variants. Published as futureagi/future-agi:<version>
#            for Distributed, Helm, the simulation runner and the EE and cloud
#            images, which `uv pip install` on top of it.
#   slim     the lean build, published as futureagi/future-agi:<version>-slim:
#            the base of futureagi/platform (Standalone).
#
# EXTRAS=none installs no optional group. The FFMPEG_FLAVOR defaults must
# match the ffmpeg-standard and ffmpeg-slim stages of Dockerfile.oss, which
# pick the ffmpeg build stage before any RUN can (deploy/tests checks it).

case "${IMAGE_VARIANT:=standard}" in
    standard)
        # The groups that were base dependencies before the image-size split
        # (pyproject.toml [project.optional-dependencies]).
        : "${EXTRAS:=sandbox,billing,ops,gcp,langchain,rabbitmq}"
        : "${FFMPEG_FLAVOR:=debian}"
        : "${WITH_GIT:=true}"
        : "${WITH_UV:=true}"
        : "${SLIM_SITE_PACKAGES:=0}"
        : "${STRIP_SO:=0}"
        : "${NLTK_DATA_PROFILE:=full}"
        ;;
    slim)
        : "${EXTRAS:=none}"
        : "${FFMPEG_FLAVOR:=minimal}"
        : "${WITH_GIT:=false}"
        : "${WITH_UV:=false}"
        : "${SLIM_SITE_PACKAGES:=1}"
        : "${STRIP_SO:=1}"
        : "${NLTK_DATA_PROFILE:=minimal}"
        ;;
    *)
        echo "IMAGE_VARIANT must be standard or slim, not '${IMAGE_VARIANT}'" >&2
        exit 1
        ;;
esac
if [ "$EXTRAS" = none ]; then
    EXTRAS=""
fi

_image_variant_check() { # name, value, allowed values...
    _name=$1
    _value=$2
    shift 2
    for _allowed in "$@"; do
        if [ "$_value" = "$_allowed" ]; then
            return 0
        fi
    done
    echo "$_name must be one of: $*; not '$_value'" >&2
    exit 1
}
case "$EXTRAS" in
    *[!a-z0-9,-]*)
        echo "EXTRAS is a comma-separated list of dependency groups, not '$EXTRAS'" >&2
        exit 1
        ;;
esac
_image_variant_check FFMPEG_FLAVOR "$FFMPEG_FLAVOR" minimal debian none
_image_variant_check WITH_GIT "$WITH_GIT" true false
_image_variant_check WITH_UV "$WITH_UV" true false
_image_variant_check SLIM_SITE_PACKAGES "$SLIM_SITE_PACKAGES" 0 1
_image_variant_check STRIP_SO "$STRIP_SO" 0 1
_image_variant_check NLTK_DATA_PROFILE "$NLTK_DATA_PROFILE" minimal full

export IMAGE_VARIANT EXTRAS FFMPEG_FLAVOR WITH_GIT WITH_UV \
    SLIM_SITE_PACKAGES STRIP_SO NLTK_DATA_PROFILE
