/**
 * GET /api/auth/me
 * -----------------
 * Returns the current user's role information.
 *
 * SECURITY:
 * - Super user determination is done server-side using the non-public
 *   SUPER_USER_EMAILS env var — never exposed to the frontend bundle.
 * - Replaces the old client-side check that used NEXT_PUBLIC_SUPER_USER_EMAILS.
 *
 * Response: { isSuperUser: boolean, isAnonymous: boolean }
 */

import { NextResponse } from "next/server";
import { getUser, isAnonymous, isSuperUser } from "@/lib/auth-helpers";

export async function GET() {
  const user = await getUser();

  if (!user) {
    return NextResponse.json(
      { isSuperUser: false, isAnonymous: true },
      { headers: { "Cache-Control": "no-store" } },
    );
  }

  return NextResponse.json(
    {
      isSuperUser: isSuperUser(user),
      isAnonymous: isAnonymous(user),
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
