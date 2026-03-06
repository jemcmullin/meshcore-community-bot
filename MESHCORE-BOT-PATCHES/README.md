# Local Patches

This directory tracks any local modifications needed to meshcore-bot that
can't be upstreamed. When updating the submodule, check each patch here
to see if it still needs to be applied.

## Format

Name patch files descriptively: `NNN-short-description.patch`

Generate a patch file from a diff:
  git diff meshcore-bot/path/to/file > MESHCORE-BOT-PATCHES/001-description.patch

Apply a patch:
  git apply MESHCORE-BOT-PATCHES/001-description.patch

## Current Patches

(none)

## Updating meshcore-bot

  git submodule update --remote
  # Review MESHCORE-BOT-PATCHES/ and reapply any still-needed changes manually
