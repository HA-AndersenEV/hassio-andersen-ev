# Changelog

## [0.12.0](https://github.com/HA-AndersenEV/hassio-andersen-ev/compare/v0.11.0...v0.12.0) (2026-09-19)


### Features

* allow reconfiguring the integration without removing it ([#91](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/91)) ([3efe22b](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/3efe22ba209207fdaa7dfb0ba093096280a8face))
* classify schedule switches and diagnostic sensors ([#88](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/88)) ([3daeea9](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/3daeea954061e373cd668d7497c0d1dfa71327c7))
* surface a repair notification if Andersen's API changes unexpectedly ([#92](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/92)) ([0c8d5b8](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/0c8d5b86092743e4b26492588fd85f97b01fe473))
* translatable entity names, icons, and error messages ([#90](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/90)) ([7dfb000](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/7dfb0001eb5464f3038d4468aad48f1f2fda65bb))


### Bug Fixes

* clean up type checking errors and enforce the Typecheck CI check ([#95](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/95)) ([04a190b](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/04a190b04e739c48e1857ce2212b2f4adf0d43c9))
* correct cost sensor currency and keep energy data live ([#99](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/99)) ([bf68e7b](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/bf68e7bb92c911a93d62e2f7bab66ceaeee461ff))

## [0.11.0](https://github.com/HA-AndersenEV/hassio-andersen-ev/compare/v0.10.0...v0.11.0) (2026-09-05)


### Features

* downloadable diagnostics with redacted credentials ([#77](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/77)) ([14c18b4](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/14c18b413db9ce81fba772f32d523c8211bbd466))
* keep charger devices in sync automatically ([#78](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/78)) ([9338af4](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/9338af4e9c7f66f5836aa199aeddaec6ea18f77a))


### Bug Fixes

* resolve a Home Assistant deprecation warning and update test tooling ([#75](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/75)) ([612c9c8](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/612c9c869fc9fd63b07251f618e4bf5f4270086b))
* stop PR title check from getting stuck on rapid rebases ([#87](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/87)) ([1fbb69f](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/1fbb69fedce140c6fe781597f82e0bc2da1493c0))

## [0.10.0](https://github.com/HA-AndersenEV/hassio-andersen-ev/compare/v0.9.0...v0.10.0) (2026-07-19)


### Features

* allow concurrent entity updates per platform ([#64](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/64)) ([b020f8f](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/b020f8f9703cd6cb8f3e0d9ccba32035bf2b9356))
* clearer error reporting during sign-in ([#58](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/58)) ([5a1dcdb](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/5a1dcdbff8c7b0aacdd12a9567f796ab9be4ecb4))
* mark charger unavailable when the API is unreachable ([#63](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/63)) ([b7524a8](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/b7524a8138c7398333097bf6377e582b59229d90))
* prompt to re-enter password when credentials expire ([#60](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/60)) ([bad9aef](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/bad9aef4615e7347980929375f81885756bf62b0))
* raise errors to the user when service actions fail ([#62](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/62)) ([26926e6](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/26926e686385f5bb69d6b3215b64b100766e5d67))

## [0.9.0](https://github.com/HA-AndersenEV/hassio-andersen-ev/compare/v0.8.1...v0.9.0) (2026-07-18)


### Features

* promote hassfest + HACS to blocking and declare Bronze ([#57](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/57)) ([54a3985](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/54a3985d1fc3cc59df0312781114a0088b71f5d4))


### Bug Fixes

* set unique id and prevent duplicate config entries ([#52](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/52)) ([7ad3def](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/7ad3def128232c9b2b3aab863308f1b77509d25f))

## [0.8.1](https://github.com/HA-AndersenEV/hassio-andersen-ev/compare/v0.8.0...v0.8.1) (2026-07-14)


### Bug Fixes

* Add UNAUTHENTICATED TransportQueryError retry with token refresh ([#35](https://github.com/HA-AndersenEV/hassio-andersen-ev/issues/35)) ([f32c117](https://github.com/HA-AndersenEV/hassio-andersen-ev/commit/f32c117fc12bdcecfa5122b498b700d10b65c2a3))

## Changelog
