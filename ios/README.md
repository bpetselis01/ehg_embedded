# iPhone gateway

Swift wire library, gateway core and iPhone app. See [docs/ios-gateway.md](../docs/ios-gateway.md).

**Status: Written.** Code and unit tests only. It has not been run against real servers and is not part of the measured results (H1 to H4).
The existing tool that could replace it is named in the [reuse ledger](../docs/licences.md#reuse-ledger); the status words are defined in [production-topology.md](../docs/production-topology.md).

**Since 11 Nov 2026** the app's enrolment screen has no server to talk to, because the custom enrolment API was removed (the `step` CLI with step-ca is the standard route). The check that validated the Swift certificate request went with it.
