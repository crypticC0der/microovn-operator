# Contributing

To make contributions to this charm, you'll need a working development setup.

The [concierge](https://github.com/canonical/concierge) utility may be useful
to get you started quickly:

```shell
sudo snap install --classic concierge
sudo concierge prepare -p machine
```

## Testing

Integration tests are implemented using the
[Jubilant](https://github.com/canonical/jubilant) framework, and can be
executed using the `check-system` or `test` targets:

```shell
make check-system
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

## Build the charms

Build the charms in this git repository using:

```shell
make
```

<!-- You may want to include any contribution/style guidelines in this document>
