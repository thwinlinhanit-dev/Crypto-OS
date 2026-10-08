/**
 * The API origin the browser calls. NEXT_PUBLIC_API_BASE is the documented
 * knob (.env.example); the fallback matches spec section 3.1 (API on 8000).
 * The two origins differ, so the API allows this list under CORS.
 */
export const API_BASE: string = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
