/*
 * winhttp-rta-probe — standalone WinHTTP TLS / WebSocket-upgrade probe.
 *
 * Purpose (Friends-world investigation, upstream issue #48): test whether
 * Wine's winhttp/secur32(GnuTLS) stack can complete an HTTPS request and a
 * WebSocket upgrade handshake against rta.xboxlive.com and related
 * Azure-fronted hosts, OUTSIDE Minecraft and WITHOUT any credentials.
 *
 * Hypothesis under test: Minecraft/XSAPI's RTA WebSocket connect attempts to
 * wss://rta.xboxlive.com/connect die at the TLS layer (WinHttpReceiveResponse
 * failing with 0x80090304 / 0x2746, "TLS fatal alert received"), so XSAPI
 * never obtains an MPSD connection id and the session write is rejected with
 * HTTP 400 ("world is full").
 *
 * The probe sends NO authentication and logs NO identifiers: every request is
 * an anonymous GET. Expected result on a healthy TLS path is an ordinary HTTP
 * status (404/401/403/...). A ReceiveResponse failure reproduces the game's
 * failure mode.
 *
 * Usage: winhttp-rta-probe.exe [iterations]
 * Build: x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
 *          -o winhttp-rta-probe.exe winhttp-rta-probe.c -lwinhttp
 */

#include <windows.h>
#include <winhttp.h>
#include <stdio.h>
#include <stdlib.h>

#define PROBE_BANNER "winhttp-rta-probe-v1"

#ifndef WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET
#define WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET 114
#endif
#ifndef WINHTTP_OPTION_SECURE_PROTOCOLS
#define WINHTTP_OPTION_SECURE_PROTOCOLS 84
#endif
#ifndef WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2
#define WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 0x00000800
#endif
#ifndef WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3
#define WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3 0x00002000
#endif

/* Relax certificate validation only: a scratch Wine prefix may lack a CA
 * bundle, and certificate trust is not the layer under test. Record-layer /
 * alert failures are unaffected by these flags. */
static const DWORD ignore_cert_flags =
    SECURITY_FLAG_IGNORE_UNKNOWN_CA |
    SECURITY_FLAG_IGNORE_CERT_DATE_INVALID |
    SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
    SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE;

struct probe_host
{
    const WCHAR *host;
    const WCHAR *path;
    const char  *label;
    BOOL         websocket;   /* also attempt a WebSocket upgrade */
};

static const struct probe_host hosts[] =
{
    { L"rta.xboxlive.com",            L"/connect", "rta",       TRUE  },
    { L"sessiondirectory.xboxlive.com", L"/",      "mpsd",      FALSE },
    { L"client.discovery.minecraft-services.net", L"/", "discovery", FALSE },
    /* hosts named by WineGDK HTTPClient.c as failing the in-game cert probe */
    { L"20ca2.playfabapi.com",        L"/",        "playfab",   FALSE },
    { L"self.events.data.microsoft.com", L"/",     "events",    FALSE },
    { L"example.com",                 L"/",        "neutral",   FALSE },
};

/* Async plumbing: libHttpClient opens its WinHTTP session with
 * WINHTTP_FLAG_ASYNC and drives the upgrade through status callbacks; Wine's
 * async request path is distinct code from the sync path, so probe both. */
struct async_ctx
{
    HANDLE done;
    DWORD  error;        /* 0 = HEADERS_AVAILABLE reached */
    BOOL   headers;
};

static void CALLBACK async_callback( HINTERNET handle, DWORD_PTR context,
                                     DWORD status, LPVOID info, DWORD length )
{
    struct async_ctx *ctx = (struct async_ctx *)context;
    (void)length;
    if (!ctx) return;
    switch (status)
    {
    case WINHTTP_CALLBACK_STATUS_SENDREQUEST_COMPLETE:
        /* async flow: request the response from the completion callback,
         * exactly as libHttpClient's callback_status_sendrequest_complete
         * does */
        if (!WinHttpReceiveResponse( handle, NULL ))
        {
            ctx->error = GetLastError();
            SetEvent( ctx->done );
        }
        break;
    case WINHTTP_CALLBACK_STATUS_HEADERS_AVAILABLE:
        ctx->headers = TRUE;
        SetEvent( ctx->done );
        break;
    case WINHTTP_CALLBACK_STATUS_REQUEST_ERROR:
    {
        WINHTTP_ASYNC_RESULT *result = (WINHTTP_ASYNC_RESULT *)info;
        ctx->error = result ? result->dwError : 1;
        SetEvent( ctx->done );
        break;
    }
    case WINHTTP_CALLBACK_STATUS_SECURE_FAILURE:
        /* informational; REQUEST_ERROR follows */
        break;
    }
}

/* Mimic XSAPI's RTA connect headers without any real credentials: XSAPI sends
 * an Authorization header ~2.5 KB long plus a Signature header. Header size
 * alone can change server/edge behavior, so replicate the shape. */
static WCHAR *build_dummy_headers( void )
{
    static WCHAR headers[4096];
    int pos = 0;
    pos += swprintf( headers + pos, 4096 - pos, L"Authorization: XBL3.0 x=0000000000000000;" );
    for (int i = 0; i < 2400; i++) headers[pos++] = L'A';
    pos += swprintf( headers + pos, 4096 - pos, L"\r\nSignature: " );
    for (int i = 0; i < 140; i++) headers[pos++] = L'B';
    headers[pos] = 0;
    return headers;
}

/* Async WebSocket upgrade attempt mirroring libHttpClient's sequence.
 * Returns TRUE when the handshake reached HEADERS_AVAILABLE (any status). */
static BOOL probe_ws_async( const struct probe_host *ph, BOOL websocket,
                            BOOL big_headers, const char *variant )
{
    HINTERNET session = NULL, connect = NULL, request = NULL;
    struct async_ctx ctx = { 0 };
    DWORD status = 0, size = sizeof(status), err = 0;
    const char *stage = "open";
    BOOL got_status = FALSE;

    ctx.done = CreateEventW( NULL, TRUE, FALSE, NULL );
    if (!ctx.done) { err = GetLastError(); goto done; }

    session = WinHttpOpen( L"winhttp-rta-probe/1.0",
                           WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                           WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS,
                           WINHTTP_FLAG_ASYNC );
    if (!session) { err = GetLastError(); goto done; }

    stage = "set-callback";
    if (WinHttpSetStatusCallback( session, async_callback,
            WINHTTP_CALLBACK_FLAG_ALL_NOTIFICATIONS, 0 )
        == WINHTTP_INVALID_STATUS_CALLBACK)
    { err = GetLastError(); goto done; }

    stage = "connect";
    connect = WinHttpConnect( session, ph->host, INTERNET_DEFAULT_HTTPS_PORT, 0 );
    if (!connect) { err = GetLastError(); goto done; }

    stage = "open-request";
    request = WinHttpOpenRequest( connect, L"GET", ph->path, NULL,
                                  WINHTTP_NO_REFERER,
                                  WINHTTP_DEFAULT_ACCEPT_TYPES,
                                  WINHTTP_FLAG_SECURE );
    if (!request) { err = GetLastError(); goto done; }

    WinHttpSetOption( request, WINHTTP_OPTION_SECURITY_FLAGS,
                      (LPVOID)&ignore_cert_flags, sizeof(ignore_cert_flags) );

    if (websocket)
    {
        stage = "ws-option";
        if (!WinHttpAddRequestHeaders( request,
                L"Sec-WebSocket-Protocol: rta.xboxlive.com.V2", (DWORD)-1,
                WINHTTP_ADDREQ_FLAG_ADD ))
        { err = GetLastError(); goto done; }
        if (!WinHttpSetOption( request, WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET,
                               NULL, 0 ))
        { err = GetLastError(); goto done; }
    }
    if (big_headers && !WinHttpAddRequestHeaders( request,
            build_dummy_headers(), (DWORD)-1, WINHTTP_ADDREQ_FLAG_ADD ))
    { err = GetLastError(); goto done; }

    stage = "send-request";
    if (!WinHttpSendRequest( request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                             WINHTTP_NO_REQUEST_DATA, 0, 0, (DWORD_PTR)&ctx ))
    { err = GetLastError(); goto done; }

    stage = "await-headers";
    if (WaitForSingleObject( ctx.done, 30000 ) != WAIT_OBJECT_0)
    { err = ERROR_TIMEOUT; goto done; }
    if (!ctx.headers) { err = ctx.error; goto done; }

    stage = "query-status";
    if (!WinHttpQueryHeaders( request,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &status, &size,
            WINHTTP_NO_HEADER_INDEX ))
    { err = GetLastError(); goto done; }

    got_status = TRUE;

done:
    if (got_status)
        printf( "%s %s %s: OK http=%lu\n", PROBE_BANNER, ph->label, variant,
                status );
    else
        printf( "%s %s %s: FAIL stage=%s err=0x%lx\n", PROBE_BANNER, ph->label,
                variant, stage, err );
    fflush( stdout );

    if (request) WinHttpCloseHandle( request );
    if (connect) WinHttpCloseHandle( connect );
    if (session)
    {
        WinHttpSetStatusCallback( session, NULL, 0, 0 );
        WinHttpCloseHandle( session );
    }
    if (ctx.done) CloseHandle( ctx.done );
    return got_status;
}

/* One HTTP(S) attempt. If websocket is set, request the upgrade the same way
 * libHttpClient does (Sec-WebSocket-Protocol + UPGRADE_TO_WEB_SOCKET option).
 * Returns TRUE when an HTTP status was obtained (TLS path healthy). */
static BOOL probe_once( const struct probe_host *ph, DWORD secure_protocols,
                        BOOL websocket, const char *variant )
{
    HINTERNET session = NULL, connect = NULL, request = NULL;
    DWORD status = 0, size = sizeof(status), err = 0;
    const char *stage = "open";
    BOOL got_status = FALSE;

    session = WinHttpOpen( L"winhttp-rta-probe/1.0",
                           WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                           WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0 );
    if (!session) { err = GetLastError(); goto done; }

    if (secure_protocols)
    {
        stage = "set-protocols";
        if (!WinHttpSetOption( session, WINHTTP_OPTION_SECURE_PROTOCOLS,
                               &secure_protocols, sizeof(secure_protocols) ))
        { err = GetLastError(); goto done; }
    }

    stage = "connect";
    connect = WinHttpConnect( session, ph->host, INTERNET_DEFAULT_HTTPS_PORT, 0 );
    if (!connect) { err = GetLastError(); goto done; }

    stage = "open-request";
    request = WinHttpOpenRequest( connect, L"GET", ph->path, NULL,
                                  WINHTTP_NO_REFERER,
                                  WINHTTP_DEFAULT_ACCEPT_TYPES,
                                  WINHTTP_FLAG_SECURE );
    if (!request) { err = GetLastError(); goto done; }

    WinHttpSetOption( request, WINHTTP_OPTION_SECURITY_FLAGS,
                      (LPVOID)&ignore_cert_flags, sizeof(ignore_cert_flags) );

    if (websocket)
    {
        stage = "ws-option";
        if (!WinHttpAddRequestHeaders( request,
                L"Sec-WebSocket-Protocol: rta.xboxlive.com.V2", (DWORD)-1,
                WINHTTP_ADDREQ_FLAG_ADD ))
        { err = GetLastError(); goto done; }
        if (!WinHttpSetOption( request, WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET,
                               NULL, 0 ))
        { err = GetLastError(); goto done; }
    }

    stage = "send-request";
    if (!WinHttpSendRequest( request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                             WINHTTP_NO_REQUEST_DATA, 0, 0, 0 ))
    { err = GetLastError(); goto done; }

    stage = "receive-response";
    if (!WinHttpReceiveResponse( request, NULL ))
    { err = GetLastError(); goto done; }

    stage = "query-status";
    if (!WinHttpQueryHeaders( request,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &status, &size,
            WINHTTP_NO_HEADER_INDEX ))
    { err = GetLastError(); goto done; }

    got_status = TRUE;

    if (websocket && status == 101)
    {
        HINTERNET ws = WinHttpWebSocketCompleteUpgrade( request, 0 );
        if (ws)
        {
            printf( "%s %s %s: HTTP 101 + complete-upgrade OK\n",
                    PROBE_BANNER, ph->label, variant );
            WinHttpWebSocketClose( ws, 1000 /* NORMAL */, NULL, 0 );
            WinHttpCloseHandle( ws );
        }
        else
            printf( "%s %s %s: HTTP 101 but complete-upgrade FAILED err=0x%lx\n",
                    PROBE_BANNER, ph->label, variant, GetLastError() );
    }

done:
    if (got_status)
        printf( "%s %s %s: OK http=%lu\n", PROBE_BANNER, ph->label, variant,
                status );
    else
        printf( "%s %s %s: FAIL stage=%s err=0x%lx\n", PROBE_BANNER, ph->label,
                variant, stage, err );
    fflush( stdout );

    if (request) WinHttpCloseHandle( request );
    if (connect) WinHttpCloseHandle( connect );
    if (session) WinHttpCloseHandle( session );
    return got_status;
}

int main( int argc, char **argv )
{
    int iterations = 5, ok = 0, fail = 0;
    if (argc > 1) iterations = atoi( argv[1] );
    if (iterations < 1) iterations = 1;

    printf( "%s starting: %d iteration(s), %u host(s)\n",
            PROBE_BANNER, iterations,
            (unsigned)(sizeof(hosts) / sizeof(hosts[0])) );
    fflush( stdout );

    for (int i = 0; i < iterations; i++)
    {
        for (size_t h = 0; h < sizeof(hosts) / sizeof(hosts[0]); h++)
        {
            const struct probe_host *ph = &hosts[h];

            /* default protocols, plain GET */
            if (probe_once( ph, 0, FALSE, "get-default" )) ok++; else fail++;

            /* async plain GET (isolates async-vs-websocket effects) */
            if (probe_ws_async( ph, FALSE, FALSE, "get-async" )) ok++; else fail++;

            /* TLS 1.2 only, plain GET */
            if (probe_once( ph, WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2,
                            FALSE, "get-tls12" )) ok++; else fail++;

            if (ph->websocket)
            {
                /* WebSocket upgrade, default protocols, sync */
                if (probe_once( ph, 0, TRUE, "ws-default" )) ok++; else fail++;

                /* WebSocket upgrade, TLS 1.2 only, sync */
                if (probe_once( ph, WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2,
                                TRUE, "ws-tls12" )) ok++; else fail++;

                /* WebSocket upgrade, async (libHttpClient's mode) */
                if (probe_ws_async( ph, TRUE, FALSE, "ws-async" )) ok++; else fail++;

                /* WebSocket upgrade, async + XSAPI-shaped auth header block */
                if (probe_ws_async( ph, TRUE, TRUE, "ws-async-hdrs" )) ok++; else fail++;
            }
        }
    }

    printf( "%s finished: ok=%d fail=%d\n", PROBE_BANNER, ok, fail );
    return fail ? 1 : 0;
}
