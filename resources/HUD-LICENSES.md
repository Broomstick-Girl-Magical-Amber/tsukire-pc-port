# HUD icon credits

The button icons the port shows for Xbox, PlayStation and keyboard (help bar of Options, Save/Load and the controls
tab; `NX_HUD_GLYPHS`) come from these free packs. Sources are in `utils/`; `tools/hud/` adapts them (recoloured to
white, resized, composed into the game's atlas layout and encoded to BC7) when `recomp_tsukihime.ps1` builds the game.
No game art is included or redistributed: the Switch icons are the game's own, read from the user's dump.

| Icons | Author | Source | License |
|---|---|---|---|
| Xbox controller icons | Alric Monteiro | https://www.figma.com/community/file/1271153059120916114/xbox-controller-icons-free | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| PlayStation controller icons | Antoine Plu | https://www.figma.com/community/file/968584708653901737/playstation-controller-icons | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Keyboard & Mouse Icon Pack (free) | Amanz | https://amanz.itch.io/keyboard-mouse-icon-pack-free | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

Changes made to the icons: cut out of the preview sheets, recoloured, resized and arranged into 384x256 atlases; some
are combined (two d-pad directions, two shoulder buttons stacked) and the Xbox View button is redrawn as a wide pill.
Keys without an icon of their own (F13-F24, Num0-Num9, unassigned buttons) are the pack's key cap with the name
written with Noto Sans JP (`resources/fonts`, OFL).

Xbox and PlayStation are trademarks of their owners. The icons only show button shapes and letters; no logos are used.
