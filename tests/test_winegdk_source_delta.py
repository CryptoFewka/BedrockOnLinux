"""Keep the native WineGDK Xbox and file-picker delta reproducible."""
# SPDX-License-Identifier: MIT

import hashlib
import re
import unittest
from pathlib import Path

from bol.config import (
    WINEGDK_SOURCE_COMMIT,
    WINEGDK_SOURCE_MANIFEST_SHA256,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build-winegdk-bullseye.sh"
CONTAINER_SCRIPT = ROOT / "scripts/build-winegdk-container.sh"
PACKAGER = ROOT / "scripts/package-engine.sh"
BUILD_WORKFLOW = ROOT / ".github/workflows/build-winegdk.yml"
ENGINE_WORKFLOW = ROOT / ".github/workflows/build-engine.yml"
RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
BASE_PATCH = (ROOT / "third_party/winegdk-r12" /
              "online-patches-after-user-ready.patch")
DELTA = ROOT / "third_party/winegdk-native5"
PATCH = DELTA / "0001-winegdk-native5-Xbox-and-file-picker-runtime.patch"
FOLLOWUP_PATCH = (
    DELTA / "0002-windows.storage-use-legacy-single-file-dialog.patch"
)
CLIENT_SURFACE_PATCH = (
    DELTA / "0005-winex11-use-client-surface-origin.patch"
)
ACHIEVEMENTS_PATCH = (
    DELTA / "0003-xgameruntime-use-windows-achievements-token.patch"
)
CONTEXT_CALLBACK_PATCH = (
    DELTA / "0004-combase-implement-context-callback.patch"
)
SOURCE_SUMS = DELTA / "SOURCE-SHA256SUMS"
CHANGED_FILES = {
    "dlls/combase/combase.c",
    "dlls/combase/combase_private.h",
    "dlls/combase/dcom.idl",
    "dlls/combase/marshal.c",
    "dlls/combase/stubmanager.c",
    "dlls/ole32/dcom.idl",
    "dlls/windows.storage.applicationdata/Makefile.in",
    "dlls/windows.storage.applicationdata/main.c",
    "dlls/windows.storage.applicationdata/private.h",
    "dlls/windows.storage.applicationdata/storagefile.c",
    "dlls/windows.storage/Makefile.in",
    "dlls/windows.storage/classes.idl",
    "dlls/windows.storage/main.c",
    "dlls/windows.storage/pickers.c",
    "dlls/windows.storage/private.h",
    "dlls/windows.storage/tests/storage.c",
    "dlls/windows.storage/vector.c",
    "dlls/xgameruntime/GDKComponent/System/User/XUser.c",
    "dlls/xgameruntime/GDKComponent/System/User/XUser.h",
    "dlls/xgameruntime/GDKComponent/System/User/DeviceAuth.c",
    "dlls/xgameruntime/GDKComponent/System/User/DeviceAuth.h",
    "dlls/xgameruntime/GDKComponent/System/User/Token.c",
    "dlls/xgameruntime/GDKComponent/System/User/Token.h",
    "dlls/xgameruntime/GDKComponent/InitInternalGDKC.c",
    "dlls/xgameruntime/GDKComponent/System/XGame.c",
    "dlls/xgameruntime/GDKComponent/System/XSystem.c",
    "dlls/xgameruntime/Makefile.in",
    "dlls/xgameruntime/main.c",
    "dlls/xgameruntime/private.h",
    "dlls/xgameruntime/tests/xgameruntime.c",
    "include/Makefile.in",
    "include/comsvcs.idl",
    "include/microsoft.ui.idl",
    "include/microsoft.windows.storage.pickers.idl",
    "include/objidlbase.idl",
    "include/xgame.idl",
    "include/xgameerr.h",
    "libs/uuid/uuid.c",
}
PINNED_SOURCE_FILES = CHANGED_FILES | {"dlls/winex11.drv/init.c"}


class WineGdkSourceDeltaTests(unittest.TestCase):
    def _constant(self, name):
        match = re.search(
            rf'^readonly {name}="([^"]+)"', SCRIPT.read_text(), re.MULTILINE)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_builder_pins_target_base_patch_and_all_changed_sources(self):
        self.assertEqual(self._constant("EXPECTED_COMMIT"),
                         WINEGDK_SOURCE_COMMIT)
        self.assertRegex(self._constant("PUBLIC_BASE_COMMIT"),
                         r"^[0-9a-f]{40}$")
        self.assertEqual(
            hashlib.sha256(BASE_PATCH.read_bytes()).hexdigest(),
            self._constant("VENDORED_BASE_PATCH_SHA256"),
        )
        self.assertEqual(
            hashlib.sha256(PATCH.read_bytes()).hexdigest(),
            self._constant("VENDORED_PATCH_SHA256"),
        )
        self.assertEqual(
            hashlib.sha256(FOLLOWUP_PATCH.read_bytes()).hexdigest(),
            self._constant("VENDORED_FOLLOWUP_PATCH_SHA256"),
        )
        self.assertEqual(
            hashlib.sha256(ACHIEVEMENTS_PATCH.read_bytes()).hexdigest(),
            self._constant("VENDORED_ACHIEVEMENTS_PATCH_SHA256"),
        )
        self.assertEqual(
            hashlib.sha256(CONTEXT_CALLBACK_PATCH.read_bytes()).hexdigest(),
            self._constant("VENDORED_CONTEXT_CALLBACK_PATCH_SHA256"),
        )
        self.assertEqual(
            hashlib.sha256(CLIENT_SURFACE_PATCH.read_bytes()).hexdigest(),
            self._constant("VENDORED_CLIENT_SURFACE_PATCH_SHA256"),
        )
        self.assertEqual(
            hashlib.sha256(SOURCE_SUMS.read_bytes()).hexdigest(),
            self._constant("SOURCE_SHA256SUMS_SHA256"),
        )
        self.assertEqual(
            hashlib.sha256(SOURCE_SUMS.read_bytes()).hexdigest(),
            WINEGDK_SOURCE_MANIFEST_SHA256,
        )
        pinned = {
            line.split("  ", 1)[1]
            for line in SOURCE_SUMS.read_text().splitlines() if line
        }
        self.assertEqual(pinned, PINNED_SOURCE_FILES)

    def test_patch_completes_native_context_and_file_picker_without_patcher(self):
        text = PATCH.read_text()
        followup = FOLLOWUP_PATCH.read_text()
        achievements = ACHIEVEMENTS_PATCH.read_text()
        context_callback = CONTEXT_CALLBACK_PATCH.read_text()
        self.assertTrue(text.startswith(f"From {WINEGDK_SOURCE_COMMIT} "))
        changed = {
            left for left, right in re.findall(
                r"^diff --git a/(\S+) b/(\S+)$",
                text + followup + achievements + context_callback,
                re.MULTILINE,
            )
            if left == right
        }
        self.assertEqual(changed, CHANGED_FILES)

        additions = "\n".join(
            line[1:] for line in (
                text + followup + achievements + context_callback
            ).splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("WineGDKLoadGameConfig", additions)
        self.assertIn("XUserGetTokenAndSignatureUtf16Data", additions)
        self.assertIn("xbl_privileges", additions)
        self.assertIn("if (sandboxIdUsed)", additions)
        self.assertIn("native Xbox app configuration ready", additions)
        self.assertIn("*maxUsers = 1", additions)
        self.assertIn("XUserGamertagComponent_ModerSuffix", additions)
        self.assertIn("result_token_len + 1", additions)
        self.assertIn("result_token_utf16_count * sizeof(WCHAR)", additions)
        self.assertIn("performing native InitializeApiImplEx2 one-time attempt",
                      additions)
        self.assertIn("INIT_ONCE", additions)
        self.assertIn("realms_token", additions)
        self.assertIn("https://pocket.realms.minecraft.net/", additions)
        self.assertIn("pocket.realms.minecraft.net", additions)
        self.assertIn("bedrock.frontend.realms.minecraft-services.net",
                      additions)
        self.assertIn("bedrock.frontendlegacy.realms.minecraft-services.net",
                      additions)
        self.assertIn(
            "RuntimeClass_Microsoft_Windows_Storage_Pickers_FileOpenPicker",
            additions,
        )
        self.assertIn("IFileOpenPickerFactory", additions)
        self.assertIn("PickSingleFileAsync", additions)
        self.assertIn("PickMultipleFilesAsync", additions)
        self.assertIn("FOS_ALLOWMULTISELECT", additions)
        self.assertIn("CLSID_FileOpenDialog", additions)
        followup_additions = "\n".join(
            line[1:] for line in followup.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        followup_deletions = "\n".join(
            line[1:] for line in followup.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        self.assertIn("IMPORTS = uuid shell32 ole32 comdlg32 combase",
                      followup_additions)
        self.assertIn("OPENFILENAMEW dialog = {0}", followup_additions)
        self.assertIn("GetOpenFileNameW( &dialog )", followup_additions)
        self.assertIn("dialog.hwndOwner = operation->hwnd",
                      followup_additions)
        self.assertIn("OFN_FILEMUSTEXIST", followup_additions)
        self.assertIn("OFN_PATHMUSTEXIST", followup_additions)
        self.assertIn("hr = show_file_dialog( operation, &result );",
                      followup_additions)
        self.assertIn("operation_complete( operation, hr, result );",
                      followup_additions)
        self.assertIn(
            "RuntimeClass_Windows_Storage_StorageFile",
            followup_additions,
        )
        self.assertIn("storage_file_statics_iid", followup_additions)
        self.assertIn("statics_GetFileFromPathAsync", followup_additions)
        self.assertIn("IID_IAsyncOperation_StorageFile",
                      followup_additions)
        self.assertIn("IID_IStorageItem", followup_additions)
        self.assertIn("item_get_Name", followup_additions)
        self.assertIn("item_get_Path", followup_additions)
        self.assertIn("HANDLE thread;", followup_deletions)
        self.assertIn("CloseHandle( operation->thread )", followup_deletions)
        self.assertIn("static DWORD WINAPI file_picker_worker",
                      followup_deletions)
        self.assertIn("operation_add_ref( operation );", followup_deletions)
        self.assertIn("CreateThread(", followup_deletions)
        self.assertIn("completed typed async operation", followup)
        self.assertIn("caller apartment", followup)
        self.assertNotIn("IFileDialog_Show( file_dialog, NULL )",
                         followup_additions)
        self.assertEqual(
            followup_deletions.count(
                "IFileDialog_Show( file_dialog, operation->hwnd )"
            ),
            1,
        )
        self.assertIn("IPickFileResult_AddRef( *results = operation->result )",
                      additions)
        self.assertNotIn("payments.realms.minecraft-services.net", additions)
        self.assertNotIn("WineGDKApplyOnlinePatches", additions)
        self.assertNotIn("VirtualProtect", additions)
        self.assertNotIn("GetModuleInformation", additions)

        deletions = "\n".join(
            line[1:] for line in PATCH.read_text().splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        self.assertIn("WineGDKApplyOnlinePatches", deletions)
        self.assertIn("VirtualProtect", deletions)

        achievement_additions = "\n".join(
            line[1:] for line in achievements.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for field in (
            '"achievements_token"',
            '"achievements_uhs"',
            '"achievements_expiry_epoch"',
        ):
            self.assertIn(field, achievement_additions)
        self.assertIn(
            'url_host_matches( url, "achievements.xboxlive.com", FALSE )',
            achievement_additions,
        )
        self.assertIn(
            "if (!achievements_request && FAILED( dowork_hr ))",
            achievement_additions,
        )
        self.assertIn(
            "user_impl->user_token, rp, &xsts_token, &token_uhs",
            achievement_additions,
        )
        self.assertIn(
            "using user-only XSTS for Xbox Achievements.",
            achievement_additions,
        )
        trace_guard = (
            "if (SUCCEEDED( dowork_hr ))\n"
            '                    TRACE( "using user-only XSTS for Xbox '
            'Achievements.\\n" );'
        )
        self.assertEqual(achievement_additions.count(trace_guard), 1)
        self.assertNotIn(
            "if (SUCCEEDED( dowork_hr ))\n"
            "                if (SUCCEEDED( dowork_hr ))",
            achievement_additions,
        )
        self.assertIn('GetJsonStringValue( object, L"uhs"', achievements)
        for strict_decimal_guard in (
            "if (!length) return E_FAIL;",
            "if (text[i] < '0' || text[i] > '9') return E_FAIL;",
            "if (parsed > (~(UINT64)0 - digit) / 10) return E_FAIL;",
            "if (!parsed) return E_FAIL;",
            "if (text[i] < L'0' || text[i] > L'9') return FALSE;",
            "if (parsed > (~(UINT64)0 - digit) / 10) return FALSE;",
            "if (!parsed) return FALSE;",
        ):
            self.assertIn(strict_decimal_guard, achievement_additions)
        self.assertIn(
            "ParseDecimalUint64( uhs_str, uhs_str_len, uhs )",
            achievement_additions,
        )
        self.assertNotIn("strtoull( uhs_str", achievement_additions)
        for lock_operation in (
            "InitializeSRWLock( &impl->achievements_lock )",
            "AcquireSRWLockShared(",
            "ReleaseSRWLockShared(",
            "AcquireSRWLockExclusive(",
            "ReleaseSRWLockExclusive(",
        ):
            self.assertIn(lock_operation, achievement_additions)
        self.assertEqual(
            achievement_additions.count(
                "RequestXstsTokenForRelyingParty(\n"
                "                            user_impl->user_token, rp, "
                "&xsts_token,"
            ),
            1,
        )
        self.assertIn(
            "now > 0 ? now + 3 * 3600 : 0",
            achievement_additions,
        )
        self.assertIn(
            "user_impl->achievements_token =\n"
            "                                    cached_token;",
            achievement_additions,
        )
        self.assertNotIn("rewrite_minecraft_achievements_url", achievements)
        self.assertNotIn("896928775", achievements)
        self.assertNotIn("1739947436", achievements)
        self.assertLess(
            achievements.index("if (achievements_request)"),
            achievements.index(
                "else if (DeviceAuth_IsInitialized() && "
                "user_impl->oauth_token"
            ),
        )

        context_additions = "\n".join(
            line[1:] for line in context_callback.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for marker in (
            "struct thread_context *context = impl_from_IContextCallback",
            "CoGetContextToken((ULONG_PTR *)&current_context)",
            "apartment_get_current_or_mta()",
            "IRundown_DoCallback",
            "IRundown_DoNonreentrantCallback",
            "RPC_E_SERVERFAULT",
        ):
            self.assertIn(marker, context_additions)
        self.assertNotIn("RoFailFastWithErrorContext", context_additions)

    def test_builder_replays_reviewed_r12_then_native_delta(self):
        text = SCRIPT.read_text()
        self.assertIn('apply --check "$VENDORED_BASE_PATCH"', text)
        self.assertIn('apply --check "$VENDORED_PATCH"', text)
        self.assertIn('apply --check "$VENDORED_FOLLOWUP_PATCH"', text)
        self.assertIn(
            'apply --check "$VENDORED_ACHIEVEMENTS_PATCH"',
            text,
        )
        self.assertIn(
            'apply --check "$VENDORED_CONTEXT_CALLBACK_PATCH"',
            text,
        )
        self.assertLess(text.index('apply "$VENDORED_BASE_PATCH"'),
                        text.index('apply --check "$VENDORED_PATCH"'))
        self.assertLess(text.index('apply "$VENDORED_PATCH"'),
                        text.index('apply --check "$VENDORED_FOLLOWUP_PATCH"'))
        self.assertLess(
            text.index('apply "$VENDORED_FOLLOWUP_PATCH"'),
            text.index('apply --check "$VENDORED_ACHIEVEMENTS_PATCH"'),
        )
        self.assertLess(
            text.index('apply "$VENDORED_ACHIEVEMENTS_PATCH"'),
            text.index('apply --check "$VENDORED_CONTEXT_CALLBACK_PATCH"'),
        )
        self.assertNotIn("VENDORED_PICKER_COMPLETION_PATCH", text)

    def test_container_builder_applies_hash_locked_followup(self):
        text = CONTAINER_SCRIPT.read_text()
        self.assertIn(
            'readonly VENDORED_FOLLOWUP_PATCH_SHA256='
            f'"{hashlib.sha256(FOLLOWUP_PATCH.read_bytes()).hexdigest()}"',
            text,
        )
        self.assertIn('apply --check "$VENDORED_FOLLOWUP_PATCH"', text)
        self.assertIn(
            'readonly VENDORED_ACHIEVEMENTS_PATCH_SHA256='
            f'"{hashlib.sha256(ACHIEVEMENTS_PATCH.read_bytes()).hexdigest()}"',
            text,
        )
        self.assertIn(
            'apply --check "$VENDORED_ACHIEVEMENTS_PATCH"',
            text,
        )
        self.assertIn(
            'readonly VENDORED_CONTEXT_CALLBACK_PATCH_SHA256='
            f'"{hashlib.sha256(CONTEXT_CALLBACK_PATCH.read_bytes()).hexdigest()}"',
            text,
        )
        self.assertIn(
            'apply --check "$VENDORED_CONTEXT_CALLBACK_PATCH"',
            text,
        )
        self.assertLess(
            text.index('archive --format=tar "$EXPECTED_COMMIT"'),
            text.index('apply --check "$VENDORED_FOLLOWUP_PATCH"'),
        )
        self.assertLess(
            text.index('apply "$VENDORED_FOLLOWUP_PATCH"'),
            text.index('apply --check "$VENDORED_ACHIEVEMENTS_PATCH"'),
        )
        self.assertLess(
            text.index('apply "$VENDORED_ACHIEVEMENTS_PATCH"'),
            text.index('apply --check "$VENDORED_CONTEXT_CALLBACK_PATCH"'),
        )
        self.assertNotIn("VENDORED_PICKER_COMPLETION_PATCH", text)

    def test_builder_can_finalize_the_verified_prefix_after_install(self):
        text = SCRIPT.read_text()
        self.assertIn(
            'local work_root="$1"\n  local prefix="$work_root/prefix"',
            text,
        )
        self.assertIn(
            'BOL_WINEGDK_INTERNAL=1 "$SCRIPT_PATH" --internal-finalize '
            '"$WORK_ROOT"',
            text,
        )
        self.assertIn(
            "source_manifest_sha256=$SOURCE_SHA256SUMS_SHA256",
            text,
        )
        self.assertIn(
            "source_manifest_sha256=$EXPECTED_SOURCE_MANIFEST_SHA256",
            CONTAINER_SCRIPT.read_text(),
        )

    def test_tier_one_identity_includes_the_vendored_source_manifest(self):
        build = BUILD_WORKFLOW.read_text()
        engine = ENGINE_WORKFLOW.read_text()
        release = RELEASE_WORKFLOW.read_text()
        self.assertIn(
            'out="winegdk-prefix-${short}-${manifest_short}.tar.gz"',
            build,
        )
        self.assertIn(
            "tag_name: winegdk-${{ env.SHORT }}-${{ env.MANIFEST_SHORT }}",
            build,
        )
        self.assertIn(
            'gh release download "winegdk-${short}-${manifest_short}"',
            engine,
        )
        self.assertIn(
            '"winegdk-${wshort}-${wmshort}" '
            '"winegdk-prefix-${wshort}-${wmshort}.tar.gz"',
            release,
        )
        self.assertIn(
            '"source_manifest_sha256": expected_source_manifest',
            PACKAGER.read_text(),
        )

    def test_packager_overlays_prefix_only_after_isolated_snapshot(self):
        text = PACKAGER.read_text()
        snapshot = 'cp -al "$ENGINE_DIR/." "$STAGED_ENGINE/"'
        overlay = (
            'cp -a --remove-destination "$WINEGDK_PREFIX/." '
            '"$STAGED_ENGINE/files/"'
        )
        self.assertIn(snapshot, text)
        self.assertIn(overlay, text)
        self.assertLess(text.index(snapshot), text.index(overlay))

    def test_packager_removes_the_legacy_i386_unix_runtime(self):
        text = PACKAGER.read_text()
        overlay = (
            'cp -a --remove-destination "$WINEGDK_PREFIX/." '
            '"$STAGED_ENGINE/files/"'
        )
        removal = (
            'rm -rf "$STAGED_ENGINE/files/lib/wine/i386-unix"'
        )
        self.assertIn(removal, text)
        self.assertIn(
            '! -e "$STAGED_ENGINE/files/lib/wine/i386-unix"', text)
        self.assertLess(text.index(overlay), text.index(removal))
        overlay_end = text.index(
            "\nfi\n\n# The combined i386+x86_64 WineGDK build",
            text.index(overlay),
        )
        self.assertLess(overlay_end, text.index(removal))
        self.assertLess(
            text.index('"$WINEGDK_PREFIX/lib/wine/i386-unix"'),
            text.index(overlay),
        )

    def test_winegdk_builders_require_a_pure_wow64_prefix(self):
        for script, prefix_name in (
            (SCRIPT, "prefix"),
            (CONTAINER_SCRIPT, "PREFIX"),
        ):
            with self.subTest(script=script.name):
                text = script.read_text()
                path = f'"${prefix_name}/lib/wine/i386-unix"'
                self.assertIn(f"! -e {path}", text)
                self.assertIn(f"! -L {path}", text)

    def test_packager_pins_current_native_source_provenance(self):
        text = PACKAGER.read_text()
        for path in (
            DELTA / "README.md",
            SOURCE_SUMS,
            PATCH,
            FOLLOWUP_PATCH,
            ACHIEVEMENTS_PATCH,
            CONTEXT_CALLBACK_PATCH,
            CLIENT_SURFACE_PATCH,
        ):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertIn(digest, text, path.name)

    def test_packager_requires_native_app_context_in_both_architectures(self):
        text = PACKAGER.read_text()
        arch_loop = text.index('for arch in "${ARCHES[@]}"; do',
                               text.index("# The native engine must expose"))
        marker = text.index(
            'has_text "$xgdk" "native Xbox app configuration ready"',
            arch_loop,
        )
        loop_end = text.index("\ndone", marker)
        self.assertLess(arch_loop, marker)
        self.assertLess(marker, loop_end)

    def test_packager_attests_and_requires_achievements_fix(self):
        text = PACKAGER.read_text()
        provenance = (
            '"native5/'
            '0003-xgameruntime-use-windows-achievements-token.patch"'
        )
        self.assertIn(provenance, text)
        self.assertIn(
            '"native5/0004-combase-implement-context-callback.patch"',
            text,
        )
        self.assertIn(
            '"native5/0005-winex11-use-client-surface-origin.patch"',
            text,
        )
        self.assertNotIn("0005-windows.storage", text)

        arch_loop = text.index('for arch in "${ARCHES[@]}"; do',
                               text.index("# The native engine must expose"))
        marker = text.index(
            '"using user-only XSTS for Xbox Achievements."',
            arch_loop,
        )
        loop_end = text.index("\ndone", marker)
        self.assertLess(arch_loop, marker)
        self.assertLess(marker, loop_end)


if __name__ == "__main__":
    unittest.main()
