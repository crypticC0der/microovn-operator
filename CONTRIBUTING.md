# Contributing

To make contributions to this charm, you'll need a working development setup.

The [concierge](https://github.com/canonical/concierge) utility may be useful
to get you started quickly:

```shell
sudo snap install --classic concierge
sudo concierge prepare -p machine
```

## Testing

We have Unit tests and standard linting tests, to run these you need to set up 
uv and tox, this is easily done like so:

```shell
snap install astral-uv --classic
uv tool install tox --with tox-uv 
```

Linting and other code checks are done as part of the ``check-code`` target. 
Unit tests are a part of the `check-system` target, however they can be run on 
their own with the `check-unit` target.

Integration tests are implemented using the
[Jubilant](https://github.com/canonical/jubilant) framework, and can be
executed using the `check-system` or `check-integration` targets:

```shell
make check-integration
```

Optionally tests may be run in parallel, for environments with sufficient
resources:

```shell
make check-system PARALLEL=4
```

In the event of failure, you may also instruct the test suite to keep
temporarily created models for further investigation.

```shell
make check-system TESTSUITEFLAGS=--keep-models
```

Finally, we have the `test` target that runs code tests, unit tests and 
integration tests.

```shell
make test PARALLEL=4 TESTSUITEFLAGS=--keep-models
```

## Build the charms

Build the charms in this git repository using:

```shell
make
```

## Clean the build environment

Remove files created during build with:

```shell
make clean
```

<!-- You may want to include any contribution/style guidelines in this document>
