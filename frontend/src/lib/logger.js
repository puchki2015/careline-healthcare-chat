/**
 * Browser logging intentionally accepts event names and safe metadata only.
 * Never pass access tokens, messages, email addresses, or patient attributes.
 */
export function logEvent(event, metadata = {}) {
  if (import.meta.env.DEV) {
    console.info(`[careline] ${event}`, metadata);
  }
}

export function logError(event, error) {
  if (import.meta.env.DEV) {
    console.error(`[careline] ${event}`, {
      errorType: error?.constructor?.name ?? 'UnknownError',
      status: error?.status ?? null,
      code: error?.code ?? null,
    });
  }
}
