!macro NSIS_HOOK_POSTINSTALL
  ; Create a desktop shortcut for public usability.
  ; This closes only the shortcut UX gap; it does not affect runtime behavior.
  CreateShortCut "$DESKTOP\\${PRODUCTNAME}.lnk" "$INSTDIR\\${PRODUCTNAME}.exe"
!macroend

