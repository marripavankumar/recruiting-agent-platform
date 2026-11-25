// Shared by the server (lib/api.ts) and the client error page. Next.js keeps an error's own
// digest when it sends a server error to the browser (the message is hidden in production), so
// this digest is how the error page tells "your role cannot see this" from a real failure.
export const FORBIDDEN_DIGEST = "RAP_FORBIDDEN";
