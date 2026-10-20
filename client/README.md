# Folklore client source snapshot

This directory contains authored native boot/runtime integration, launcher, platform and local-build tools from the current development source. It is a developer reference, not an installer. The required game-derived outputs are created privately from an owner's copy and are not shipped here. The development builder still has unresolved portability and input-generation requirements described in [SOURCE_PREVIEW.md](../docs/SOURCE_PREVIEW.md).

The native runtime integration is distributed under GPL-2.0-only as part of the combined runtime. [COPYING.RPCS3](COPYING.RPCS3) contains that license. Original standalone launcher/build tools and tests not subject to a component license use the repository's [personal-use license](../LICENSE). Existing third-party notices and legal rights take precedence. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Launcher artwork is omitted. The PyInstaller spec is retained for development reference; it is not a tested public package recipe. Do not run development scripts against your only save profile, or assume historical diagnostic commands are install instructions.
