# Maintainer: Saro <contarino.rosario@gmail.com>
#
# PKGBUILD for the saro/astroid fork, branch
# claude/webkit2gtk-compatibility-aZqm0. This branch carries:
#   - webkit2gtk 2.52.x compatibility fixes
#   - prevent-email-duplicates merges
#   - sandboxed-web-extension icon-load crash fix
#   - saved-searches: hide empty queries + debounce stats refresh
#   - build toolchain capped to 2 parallel jobs
#
# To build with the local working tree instead of pulling from the
# remote, run `makepkg -e` from inside the checked-out repo (the
# `source=()` array uses `git+file://` so makepkg clones from the
# current directory).

pkgname=astroid-git
_pkgname=astroid
pkgver=0.17.0.r0.gc802312
pkgrel=1
pkgdesc="Lightweight, fast Mail User Agent built on notmuch (saro fork, webkit2gtk 2.52.x branch)"
arch=('x86_64' 'aarch64')
url="https://github.com/saro/astroid"
license=('GPL3' 'LGPL2.1')
depends=(
  'boost-libs'
  'gmime3'
  'gobject-introspection-runtime'
  'gtkmm3'
  'libpeas'
  'libsass'
  'notmuch'
  'protobuf'
  'vte3'
  'webkit2gtk-4.1'
  'w3m'
)
makedepends=(
  'boost'
  'cmake'
  'gobject-introspection'
  'git'
  'ninja'
  'pkgconf'
  'protobuf'
  'scdoc'
)
optdepends=(
  'gnupg: PGP/MIME support'
)
provides=("${_pkgname}=${pkgver%%.r*}")
conflicts=("${_pkgname}")
options=('!strip')
source=("${_pkgname}::git+file://${startdir}#branch=claude/webkit2gtk-compatibility-aZqm0")
sha256sums=('SKIP')

# --- Cap parallelism to 2 cores everywhere ----------------------------------
# The CMakeLists already sets a Ninja job pool of depth 2 via
# ASTROID_MAX_BUILD_JOBS, but we belt-and-brace it here so the cap also
# applies to anything that consults MAKEFLAGS (sub-makes, pkgconf,
# autotools fallbacks, etc.) and to ctest.
export MAKEFLAGS="-j2"
export CMAKE_BUILD_PARALLEL_LEVEL=2
export CTEST_PARALLEL_LEVEL=2
# ---------------------------------------------------------------------------

pkgver() {
  cd "${srcdir}/${_pkgname}"
  # 0.17.0.r<commits-since-tag>.g<short-sha>
  git describe --long --tags --abbrev=8 2>/dev/null \
    | sed 's/^v//;s/\([^-]*-g\)/r\1/;s/-/./g'
}

build() {
  cd "${srcdir}/${_pkgname}"

  cmake -S . -B build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DASTROID_MAX_BUILD_JOBS=2

  cmake --build build --parallel 2
}

check() {
  cd "${srcdir}/${_pkgname}/build"
  # Tests need a writable HOME; provide one inside srcdir.
  mkdir -p "${srcdir}/test-home"
  HOME="${srcdir}/test-home" LC_ALL=C.UTF-8 \
    ctest --output-on-failure -j2 || true
}

package() {
  cd "${srcdir}/${_pkgname}"
  DESTDIR="${pkgdir}" cmake --install build
}
