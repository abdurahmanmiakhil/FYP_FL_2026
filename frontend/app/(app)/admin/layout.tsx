"use client";

import { ShieldX } from "lucide-react";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/clinical";
import { useAuth } from "@/lib/auth";

/** Admin pages (the model card is readable by every clinician). The API enforces roles too. */
export default function AdminLayout({ children }: { children: ReactNode }) {
  const { hasRole } = useAuth();
  const pathname = usePathname();
  if (!pathname.startsWith("/admin/model") && !hasRole("admin")) {
    return (
      <EmptyState icon={ShieldX} title="Administrators only">
        You do not have access to this page.
      </EmptyState>
    );
  }
  return <>{children}</>;
}
