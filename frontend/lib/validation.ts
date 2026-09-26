import { z } from "zod";

/** Mirrors the API password policy (ASVS 2.1): >= 12 chars and 3 of 4 character classes. */
export const passwordSchema = z
  .string()
  .min(12, "At least 12 characters")
  .max(128, "At most 128 characters")
  .refine((v) => [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(v)).length >= 3, {
    message: "Use at least three of: lower case, upper case, digit, symbol",
  });
