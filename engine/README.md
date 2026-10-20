# Engine source snapshot

This is the Folklore development fork of [PS3Recomp](https://github.com/sp00nznet/ps3recomp), exported without private Git history or captured game payloads. Current controller and rendering changes are included. Read [the source-preview notes](../docs/SOURCE_PREVIEW.md) before trying to build a game.

The original upstream code retains [its MIT license](LICENSE). This combined runtime also contains GPL-2.0-only material derived from or following RPCS3. It is distributed under GPL-2.0-only as a combined work, with the original permissive/component notices retained. See [COPYING.RPCS3](COPYING.RPCS3) and [third-party notices](../THIRD_PARTY_NOTICES.md). The repository's custom personal-use restrictions do not apply to the GPL-covered runtime.

An engine library build is not a complete Folklore player build. Game-derived translation units and private generation metadata are intentionally absent. External dependencies and some generated shader inputs require separate setup. No compiled game or NVIDIA runtime DLL is supplied.
