"""
Improved support for Microsoft Visual C++ compilers.

Known supported compilers:
--------------------------
Microsoft Visual C++ 9.0:
    Microsoft Visual C++ Compiler for Python 2.7 (x86, amd64)

Microsoft Visual C++ 14.X:
    Microsoft Visual C++ Build Tools 2015 (x86, x64, arm)
    Microsoft Visual Studio Build Tools 2017 (x86, x64, arm, arm64)
    Microsoft Visual Studio Build Tools 2019 (x86, x64, arm, arm64)

This may also support compilers shipped with compatible Visual Studio versions.
"""

from itertools import count
from os.path import join, isfile, isdir
import sys
import platform
import subprocess
import distutils.errors
from setuptools.extern.packaging.version import LegacyVersion

from .monkey import get_unpatched

if platform.system() == 'Windows':
    from setuptools.extern.six.moves import winreg
    from os import environ
else:
    # Mock winreg and environ so the module can be imported on this platform.

    class winreg:
        HKEY_USERS = None
        HKEY_CURRENT_USER = None
        HKEY_LOCAL_MACHINE = None
        HKEY_CLASSES_ROOT = None

    environ = dict()

_msvc9_suppress_errors = (
    # msvc9compiler isn't available on some platforms
    ImportError,

    # msvc9compiler raises DistutilsPlatformError in some
    # environments. See #1118.
    distutils.errors.DistutilsPlatformError,
)

try:
    from distutils.msvc9compiler import Reg
except _msvc9_suppress_errors:
    pass


def msvc9_find_vcvarsall(version):
    """
    Patched "distutils.msvc9compiler.find_vcvarsall" to use the standalone
    compiler build for Python
    (VCForPython / Microsoft Visual C++ Compiler for Python 2.7).

    Fall back to original behavior when the standalone compiler is not
    available.

    Redirect the path of "vcvarsall.bat".

    Parameters
    ----------
    version: float
        Required Microsoft Visual C++ version.

    Return
    ------
    str
        vcvarsall.bat path
    """
    vc_base = r'Software\%sMicrosoft\DevDiv\VCForPython\%0.1f'
    key = vc_base % ('', version)
    try:
        # Per-user installs register the compiler path here
        productdir = Reg.get_value(key, "installdir")
    except KeyError:
        try:
            # All-user installs on a 64-bit system register here
            key = vc_base % ('Wow6432Node\\', version)
            productdir = Reg.get_value(key, "installdir")
        except KeyError:
            productdir = None

    if productdir:
        vcvarsall = join(productdir, "vcvarsall.bat")
        if isfile(vcvarsall):
            return vcvarsall

    return get_unpatched(msvc9_find_vcvarsall)(version)


def msvc9_query_vcvarsall(ver, *args, **kwargs):
    """
    Patched "distutils.msvc9compiler.query_vcvarsall" to improve error message.
    """
    try:
        return get_unpatched(msvc9_query_vcvarsall)(ver, *args, **kwargs)
    except distutils.errors.DistutilsPlatformError as exc:
        _augment_exception(exc, ver)
        raise


def _msvc14_find_vc2015():
    """Python 3.8 "distutils/_msvccompiler.py" backport"""
    try:
        key = winreg.OpenKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Microsoft\VisualStudio\SxS\VC7",
            access=winreg.KEY_READ | winreg.KEY_WOW64_32KEY
        )
    except OSError:
        return None, None

    best_version = 0
    best_dir = None
    with key:
        for i in itertools.count():
            try:
                v, vc_dir, vt = winreg.EnumValue(key, i)
            except OSError:
                break
            if v and vt == winreg.REG_SZ and isdir(vc_dir):
                try:
                    version = int(float(v))
                except (ValueError, TypeError):
                    continue
                if version >= 14 and version > best_version:
                    best_version, best_dir = version, vc_dir
    return best_version, best_dir


def _msvc14_find_vc2017():
    """Python 3.8 "distutils/_msvccompiler.py" backport

    Returns "15, path" based on the result of invoking vswhere.exe
    If no install is found, returns "None, None"

    The version is returned to avoid unnecessarily changing the function
    result. It may be ignored when the path is not None.

    If vswhere.exe is not available, by definition, VS 2017 is not
    installed.
    """
    root = environ.get("ProgramFiles(x86)") or environ.get("ProgramFiles")
    if not root:
        return None, None

    try:
        path = subprocess.check_output([
            join(root, "Microsoft Visual Studio", "Installer", "vswhere.exe"),
            "-latest",
            "-prerelease",
            "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property", "installationPath",
            "-products", "*",
        ]).decode(encoding="mbcs", errors="strict").strip()
    except (subprocess.CalledProcessError, OSError, UnicodeDecodeError):
        return None, None

    path = join(path, "VC", "Auxiliary", "Build")
    if isdir(path):
        return 15, path

    return None, None


PLAT_SPEC_TO_RUNTIME = {
    'x86': 'x86',
    'x86_amd64': 'x64',
    'x86_arm': 'arm',
    'x86_arm64': 'arm64'
}


def _msvc14_find_vcvarsall(plat_spec):
    """Python 3.8 "distutils/_msvccompiler.py" backport"""
    _, best_dir = _msvc14_find_vc2017()
    vcruntime = None

    if plat_spec in PLAT_SPEC_TO_RUNTIME:
        vcruntime_plat = PLAT_SPEC_TO_RUNTIME[plat_spec]
    else:
        vcruntime_plat = 'x64' if 'amd64' in plat_spec else 'x86'

    if best_dir:
        vcredist = join(best_dir, "..", "..", "redist", "MSVC", "**",
                        vcruntime_plat, "Microsoft.VC14*.CRT",
                        "vcruntime140.dll")
        try:
            import glob
            vcruntime = glob.glob(vcredist, recursive=True)[-1]
        except (ImportError, OSError, LookupError):
            vcruntime = None

    if not best_dir:
        best_version, best_dir = _msvc14_find_vc2015()
        if best_version:
            vcruntime = join(best_dir, 'redist', vcruntime_plat,
                             "Microsoft.VC140.CRT", "vcruntime140.dll")

    if not best_dir:
        return None, None

    vcvarsall = join(best_dir, "vcvarsall.bat")
    if not isfile(vcvarsall):
        return None, None

    if not vcruntime or not isfile(vcruntime):
        vcruntime = None

    return vcvarsall, vcruntime


def _msvc14_get_vc_env(plat_spec):
    """Python 3.8 "distutils/_msvccompiler.py" backport"""
    if "DISTUTILS_USE_SDK" in environ:
        return {
            key.lower(): value
            for key, value in environ.items()
        }

    vcvarsall, vcruntime = _msvc14_find_vcvarsall(plat_spec)
    if not vcvarsall:
        raise distutils.errors.DistutilsPlatformError(
            "Unable to find vcvarsall.bat"
        )

    try:
        out = subprocess.check_output(
            'cmd /u /c "{}" {} && set'.format(vcvarsall, plat_spec),
            stderr=subprocess.STDOUT,
        ).decode('utf-16le', errors='replace')
    except subprocess.CalledProcessError as exc:
        raise distutils.errors.DistutilsPlatformError(
            "Error executing {}".format(exc.cmd)
        )

    env = {
        key.lower(): value
        for key, _, value in
        (line.partition('=') for line in out.splitlines())
        if key and value
    }

    if vcruntime:
        env['py_vcruntime_redist'] = vcruntime
    return env


def msvc14_get_vc_env(plat_spec):
    """
    Patched "distutils._msvccompiler._get_vc_env" for support extra
    Microsoft Visual C++ 14.X compilers.

    Set environment without use of "vcvarsall.bat".

    Parameters
    ----------
    plat_spec: str
        Target architecture.

    Return
    ------
    dict
        environment
    """
    # Try to get environment from vcvarsall.bat (Classical way)
    try:
        return get_unpatched(msvc14_get_vc_env)(plat_spec)
    except distutils.errors.DistutilsPlatformError:
        # Pass error Vcvarsall.bat is missing
        pass

    # If error, try to set environment directly
    try:
        return _msvc14_get_vc_env(plat_spec)
    except distutils.errors.DistutilsPlatformError as exc:
        _augment_exception(exc, 14.0)
        raise


def msvc14_gen_lib_options(*args, **kwargs):
    """
    Patched "distutils._msvccompiler.gen_lib_options" for fix
    compatibility between "numpy.distutils" and "distutils._msvccompiler"
    (for Numpy < 1.11.2)
    """
    if "numpy.distutils" in sys.modules:
        import numpy as np
        if LegacyVersion(np.__version__) < LegacyVersion('1.11.2'):
            return np.distutils.ccompiler.gen_lib_options(*args, **kwargs)
    return get_unpatched(msvc14_gen_lib_options)(*args, **kwargs)


def _augment_exception(exc, version):
    """
    Add details to the exception message to help guide the user
    as to what action will resolve it.
    """
    # Error if MSVC++ directory not found or environment not set
    message = exc.args[0]

    if "vcvarsall" in message.lower() or "visual c" in message.lower():
        # Special error message if MSVC++ not installed
        tmpl = 'Microsoft Visual C++ {version:0.1f} is required.'
        message = tmpl.format(**locals())
        msdownload = 'www.microsoft.com/download/details.aspx?id=%d'
        if version == 9.0:
            # For VC++ 9.0 redirect user to Vc++ for Python 2.7 :
            # This redirection link is maintained by Microsoft.
            # Contact vspython@microsoft.com if it needs updating.
            message += ' Get it from http://aka.ms/vcpython27'
        elif version >= 14.0:
            # For VC++ 14.X Redirect user to latest Visual C++ Build Tools
            message += (' Get it with "Build Tools for Visual Studio": '
                        r'https://visualstudio.microsoft.com/downloads/')

    exc.args = (message, )
