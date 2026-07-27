# WineGDK native Xbox and WinAppSDK file-picker delta

This engine targets WineGDK commit
`75637b674e1f191e65753663c4c0c32bea05ba6e`, on top of the reviewed r12
commit `432f414b251cc6d668404825a1d0f05eca807a70`.

The cumulative delta retains the native GDK identity, XUser, Xbox context and
Realms implementation developed in the earlier native delta. It also implements the
WinAppSDK 1.8 `Microsoft.Windows.Storage.Pickers.FileOpenPicker` runtime class
inside Wine's `windows.storage.dll` for both PE architectures.

The picker implementation provides the exact WinRT ABI used by Minecraft,
including the `Microsoft.UI.WindowId` factory, validated file-type filters,
single- and multiple-file selection, immutable result vectors, cancellation,
and `PickFileResult.Path`.
It delegates the desktop UI to Wine's `IFileOpenDialog`; closing the chooser is
reported as a successful null result instead of raising an exception. Async
results and completion delegates have explicit ownership rules to avoid the
use-after-free and reference-cycle hazards found in earlier prototypes.
The follow-up `0002` patch routes single-file selection through
`GetOpenFileNameW`. Wine's Common Item Dialog can destroy itself before
initialization inside Minecraft; the legacy dialog avoids that failing path
without changing the WinRT async result ABI. The modal chooser runs on the
calling apartment and returns an already-completed operation, so C++/WinRT
installs and invokes its completion handler without a cross-apartment
thread-pool callback. Multiple-file selection remains asynchronous on
`IFileOpenDialog`, with its private STA kept alive through completion.
Minecraft then converts the returned path with
`Windows.Storage.StorageFile.GetFileFromPathAsync`. The same patch implements
that missing static operation and the `IStorageItem` name and path properties
used by the game, without adding unused stream support.

The former Minecraft process-memory patcher remains removed. Online state
comes from XGame, XUser and XSAPI, while world and skin imports use the native
WinRT picker rather than a package-identity shim.

The `0003` patch keeps Minecraft's Windows Achievements URL and title ID intact.
For the exact `achievements.xboxlive.com` host it selects a separately minted
user-only XSTS token and its matching user hash, with a safe in-Wine user-only
fallback. Cached and live user hashes are accepted only as non-zero decimal
`uint64` values. The in-memory fallback cache is synchronized, refreshed on
demand and replaced after a forced refresh, while Android SISU tokens remain in
use for the services that require title binding, including PlayFab, multiplayer,
Realms and licensing.

The `0004` patch backports Wine's complete `IContextCallback` implementation
and its required COM interfaces for other WinRT continuations that cross
apartments.

The `0005` patch keeps the X11 client surface in client coordinates instead
of offsetting it by the Win32 visible-window frame. This prevents fullscreen
render surfaces from being placed inside their X11 parent, which otherwise
exposes the parent background along the top and left edges. It is a narrow
semantic backport of the client-surface geometry behavior introduced by
upstream Wine commit `b868cd31d6b`.

`SOURCE-SHA256SUMS` pins every source file changed by the cumulative r12 to
native5 delta and its follow-ups. The Bullseye builder applies the reviewed r12
and native patches when the target commit is unavailable, always applies `0002`
through `0005`, then verifies the complete resulting source tree.
