# UruManifest
[![Maintainability](https://api.codeclimate.com/v1/badges/2292f18dae31a985d794/maintainability)](https://codeclimate.com/github/Hoikas/UruManifest/maintainability)

Cross platform tool to generate a complete Myst Online: Uru Live file server from a set of game assets and gather packages.

## Dependencies
- [Python](https://www.python.org)
- [libHSPlasma](https://github.com/H-uru/libHSPlasma) - Plasma file IO library

## Related Projects
- [moul-assets](https://github.com/H-uru/moul-assets) - Compiled Uru game assets
- [moul-scripts](https://github.com/H-uru/moul-scripts) - Source Uru script assets

## Installing
UruManifest requires Python 3.6 or higher. If you are using Python 3.6, you will need to install the backport of the `dataclasses` module from Python 3.7 by running `pip install dataclasses`. You will also need to compile and install libHSPlasma's PyHSPlasma bindings for Python 3.

Additionally, to build a Python.pak for your shard, you will need to have the version of Python used by Uru itself. For H-uru based Uru clients, this is Python 2.7. For Cyan's Myst Online: Uru Live, this is currently Python 2.3. No additional modules or extensions are needed for this version of Python.

## Getting Started
UruManifest uses a configuration file to facillitate iteration. To generate a default configuration file, execute `python urumanifest dumpconfig`. The `--config` argument can be specified if you wish to use a filename other than `./config.ini`. These values should be tweaked to match your particular configuration.

UruManifest assumes you will be using the standard moul-scripts and moul-assets repositories to generate your file server. If this is not the case, you should familize yourself with the layout of these repositories due to this assumption.

When you are ready to generate your file server, you may do so by executing `python urumanifest generate`. The `--dry-run` argument may be specified to simulate the generation of a file server. Note that this is an argument of the `generate` command, so the proper specification is `python urumanifest generate --dry-run`.

Note that UruManifest assumes that it owns the output. You should never tweak it manually. Additionally, if you already have a generated file server via another method, it is suggested that you discard this and allow UruManifest to generate the file server afresh to prevent conflicts. If the output every needs to be completely regenerated, use the `--force` argument.

## Gather Packages
UruManifest allows Cyan-style [gather packages](http://account.mystonline.com/download/AssetSubmissionExample.zip) to be imported into the client directory. These gather packages may contain file replacements, new ages, clients, patchers, and redistributable installers.

**NOTE**: UruManifest is currently unable to automate detection of client executables, libraries, and redistributables. Therefore, it is required to create gather packages for these items for them to be included in the generated file server.

UruManifest supports additional sections in and assigns additional meaning to the gather control JSON file:
- `external`
    - These files are assumed to be for Windows x86, eg `*.exe` and `*.dll`.
    - These files are not listed in the internal client manifest.
- `internal` - **NEW**
    - These files are assumed to be for Windows x86, eg `*.exe` and `*.dll`.
    - These files are not listed in the external client manifest.
- `mac`
    - These files are used by the legacy TransGaming Cider Wrapper, not a native mac client.
- `prereq` - **NEW**
    - These files are assumed to be ***executables*** for Windows x86, eg `*.exe` and `*.msi`.
    - These files are listed in the *DependencyPatcher*, *ExternalPatcher*, and *InternalPatcher* manifests.

## Server Considerations
Currently, only DirtSand and MOSS servers are supported.

### MOSS
MOSS tries to be "clever" in many ways, resulting in some considerations if you plan to use UruManifest to generate your MOSS server data. Common stumbling blocks are enumerated below.

#### Auth Configuation
MOSS allows shipping different sets of "secure" files to different accounts. This means that the value of MOSS's `auth_download_dir` will differ from UruManifest's `output.lists`. You will want to set UruManifest's `lists` directory to a subdirectory of MOSS's `auth_download_dir` named `default`.

#### Secure Manifest
H-uru clients download the Python.pak and SDL files via the SecurePreloader file manifest. However, MOSS has a setting that may prevent clients from logging in if these files are not downloaded via the MOSS auth server. Therefore, you may need to set `server.secure_manifest` to `false` in the UruManifest configuation to work around this strange design decision in MOSS.
