# UruManifest
![CI](https://github.com/Hoikas/UruManifest/workflows/CI/badge.svg)
[![Maintainability](https://api.codeclimate.com/v1/badges/2292f18dae31a985d794/maintainability)](https://codeclimate.com/github/Hoikas/UruManifest/maintainability)

Cross platform tool to generate a complete Myst Online: Uru Live file server from a set of game
assets and gather packages.

## Dependencies
- [Python](https://www.python.org) 3.6 or higher
- [pybind11](https://github.com/pybind11/pybind11)

## Related Projects
- [moul-assets](https://github.com/H-uru/moul-assets) - Compiled Uru game assets
- [Plasma](https://github.com/H-uru/Plasma) - Plasma engine sources, including scripts

## Installing
Clone the repository into the directory of your choice. For the best performance, you will need
to build UruManifest's crypto module by executing `pip3 install .` from the directory that you
cloned the project into. Note that this module uses pybind11 and therefore requires a C++11
compliant compiler. This is module optional, however, and UruManifest will function correctly albiet
more slowly without it. If you are using Python 3.6 and are not building the module, then you will
need to manually install the `dataclasses` backport from Python 3.7 by executing `pip3 install dataclasses`.

Additionally, to build a Python.pak for your shard, you will need to have the version of Python used
by Uru itself. For H-uru based Uru clients, this is Python 3.8. For Cyan's Myst Online: Uru Live,
this is currently Python 2.3. No additional modules or extensions are needed for this version of Python.

## Getting Started
UruManifest uses a configuration file to facillitate iteration. To generate a default configuration
file, execute `python urumanifest dumpconfig`. The `--config` argument can be specified if you wish
to use a filename other than `./config.ini`. These values should be tweaked to match your particular configuration.

UruManifest assumes you will be using the standard moul-scripts and moul-assets repositories to
generate your file server. If this is not the case, you should familize yourself with the layout of
these repositories due to this assumption.

When you are ready to generate your file server, you may do so by executing `python urumanifest generate`.
Once you have generated a file server, UruManifest provides a few ways to help you deploy updates to
your shard. The easiest and recommended option is to add any changed files directly into the "source"s
given in the configuration file and rerun `python urumanifest generate`. This will trigger UruManifest
to examine the previously generated file server and either copy changes or delete removed assets from
the file server. For more control over the process, supply the `--stage` argument by executing
`python urumanifest generate --stage` to instead stage the delta into the directory structure
specified in the `stage` section of the config file. UruManifest can also provide you with more details
about its plan of action by specifying the `--debug` argument: `python urumanifest --debug generate --stage`.
If you would like to simply test the update process to make sure nothing breaks, use the `--dry-run`
argument: `python urumanifest generate --dry-run`. Note that the `--dry-run` and `--stage` arguments
are mutually exclusive.

Note that UruManifest assumes that it owns the output. You should never tweak it manually.
Additionally, if you already have a generated file server via another method, it is suggested that
you discard this and allow UruManifest to generate the file server afresh to prevent conflicts.
If the output every needs to be completely regenerated, use the `--force` argument.

## Gather Packages
UruManifest allows Cyan-style [gather packages](http://account.mystonline.com/download/AssetSubmissionExample.zip)
to be imported into the client directory. These gather packages may contain file replacements, new
ages, clients, patchers, and redistributable installers.

**NOTE**: UruManifest is currently unable to automate detection of client executables, libraries,
and redistributables. Therefore, it is required to create gather packages for these items for them
to be included in the generated file server.

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
MOSS tries to be "clever" in many ways, resulting in some considerations if you plan to use
UruManifest to generate your MOSS server data. Common stumbling blocks are enumerated below.

#### Auth Configuation
MOSS allows shipping different sets of "secure" files to different accounts. This means that the
value of MOSS's `auth_download_dir` will differ from UruManifest's `output.lists`. You will want to
set UruManifest's `lists` directory to a subdirectory of MOSS's `auth_download_dir` named `default`.

#### Secure Manifest
H-uru clients download the Python.pak and SDL files via the SecurePreloader file manifest. However,
MOSS has a setting that may prevent clients from logging in if these files are not downloaded via
the MOSS auth server. Therefore, you may need to set `server.secure_manifest` to `false` in the
UruManifest configuation to work around this strange design decision in MOSS.

#### Game Configuration
MOSS tries to be helpful to age creators by allowing the game server to load each age's SDL when the
age starts. Unfortunately, the execution of this idea is fragile and makes the storage of SDL files
extremely fragile. UruManifest will copy both the decrypted .age and .sdl to MOSS if the directories
are given. For age files, you will want to set `server.age_directory` to the `age` subdirectory of
the `game_data_dir`. For SDL, you will want to set the `server.sdl_directory` to the `SDL/common`
subdirectory of the `game_data_dir`. Any other subdirectories should be removed. This will require
that MOSS be restarted any time you run an update -- but you should never deploy an update while
the server is running, anyway.
