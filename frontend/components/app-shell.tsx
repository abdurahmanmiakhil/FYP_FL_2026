"use client";

import {
  Activity,
  BookOpen,
  BrainCircuit,
  FileStack,
  LayoutDashboard,
  LogOut,
  Menu,
  Monitor,
  Moon,
  ScrollText,
  Settings,
  ShieldAlert,
  Sun,
  Upload,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import type { Role } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: typeof Activity;
  roles?: Role[];
  exact?: boolean;
}

const NAV: { title: string; items: NavItem[] }[] = [
  {
    title: "Clinical",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, exact: true },
      { href: "/cases", label: "Cases", icon: FileStack, exact: true },
      { href: "/cases/new", label: "New upload", icon: Upload, roles: ["pathologist", "urologist", "admin"] },
    ],
  },
  {
    title: "Administration",
    items: [
      { href: "/admin/users", label: "Users", icon: Users, roles: ["admin"] },
      { href: "/admin/audit", label: "Audit trail", icon: ScrollText, roles: ["admin"] },
      { href: "/admin/model", label: "Model card", icon: BrainCircuit },
    ],
  },
  {
    title: "Help",
    items: [
      { href: "/about", label: "About the model", icon: BookOpen },
      { href: "/settings", label: "Account settings", icon: Settings },
    ],
  },
];

export function DisclaimerBanner() {
  return (
    <div
      role="note"
      className="flex items-center justify-center gap-2 border-b border-warn/30 bg-warn-bg px-4 py-1.5 text-center text-xs font-medium text-warn sm:text-sm"
    >
      <ShieldAlert className="size-4 shrink-0" aria-hidden />
      AI decision support only. Final diagnosis requires a pathologist.
    </div>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { user } = useAuth();
  return (
    <nav aria-label="Main" className="space-y-6">
      {NAV.map((group) => {
        const items = group.items.filter((i) => !i.roles || (user && i.roles.includes(user.role)));
        if (!items.length) return null;
        return (
          <div key={group.title}>
            <p className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {group.title}
            </p>
            <ul className="space-y-0.5">
              {items.map((item) => {
                const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                        active
                          ? "bg-primary/10 text-primary"
                          : "text-muted-foreground hover:bg-accent hover:text-foreground",
                      )}
                    >
                      <item.icon className="size-4" aria-hidden />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function ThemeMenuItems() {
  const { setTheme } = useTheme();
  return (
    <>
      <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">Theme</DropdownMenuLabel>
      <DropdownMenuItem onSelect={() => setTheme("light")}>
        <Sun aria-hidden /> Light
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={() => setTheme("dark")}>
        <Moon aria-hidden /> Dark
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={() => setTheme("system")}>
        <Monitor aria-hidden /> System
      </DropdownMenuItem>
    </>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  if (!user) return <Skeleton className="h-9 w-40" />;
  const initials = user.full_name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="h-10 gap-3 px-2" aria-label={`Account menu for ${user.full_name}`}>
          <span className="hidden text-right sm:block">
            <span className="block text-sm font-medium leading-tight">{user.full_name}</span>
            <span className="block text-xs capitalize text-muted-foreground">
              {user.role} - {user.hospital}
            </span>
          </span>
          <span className="flex size-8 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
            {initials}
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel>
          <span className="block">{user.full_name}</span>
          <span className="block text-xs font-normal text-muted-foreground">{user.email}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings">
            <Settings aria-hidden /> Account settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <ThemeMenuItems />
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void logout()}>
          <LogOut aria-hidden /> Sign out
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void logout({ everywhere: true })}>
          <LogOut aria-hidden /> Sign out on all devices
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function DeniedToast() {
  const params = useSearchParams();
  useEffect(() => {
    if (params.get("denied")) toast.error("That page is only available to administrators.");
  }, [params]);
  return null;
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  useEffect(() => setOpen(false), [pathname]);

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to main content
      </a>
      <DisclaimerBanner />
      <div className="flex flex-1">
        <aside className="hidden w-64 shrink-0 border-r bg-card lg:block">
          <div className="sticky top-0 flex h-screen flex-col gap-6 p-4">
            <Link href="/dashboard" className="flex items-center gap-3 px-2 py-1">
              <Logo />
              <span className="text-lg font-semibold tracking-tight">GleasonAI</span>
            </Link>
            <NavLinks />
          </div>
        </aside>
        {open && (
          <div
            className="fixed inset-0 z-40 bg-black/50 lg:hidden"
            onClick={() => setOpen(false)}
            aria-hidden
          />
        )}
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-50 w-72 border-r bg-card p-4 transition-transform lg:hidden",
            open ? "translate-x-0" : "-translate-x-full",
          )}
          aria-hidden={!open}
        >
          <div className="mb-6 flex items-center justify-between">
            <span className="flex items-center gap-3">
              <Logo />
              <span className="font-semibold">GleasonAI</span>
            </span>
            <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Close menu">
              <X aria-hidden />
            </Button>
          </div>
          <NavLinks onNavigate={() => setOpen(false)} />
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b bg-card/95 px-4 backdrop-blur sm:px-6">
            <Button
              variant="ghost"
              size="icon"
              className="lg:hidden"
              onClick={() => setOpen(true)}
              aria-label="Open menu"
              aria-expanded={open}
            >
              <Menu aria-hidden />
            </Button>
            <div className="flex-1" />
            <UserMenu />
          </header>
          <main id="main" tabIndex={-1} className="flex-1 px-4 py-6 focus:outline-none sm:px-6">
            {isLoading || !user ? (
              <div className="space-y-4" aria-busy="true" aria-label="Loading">
                <Skeleton className="h-8 w-64" />
                <Skeleton className="h-32 w-full" />
                <Skeleton className="h-64 w-full" />
              </div>
            ) : (
              children
            )}
          </main>
        </div>
      </div>
      <Suspense>
        <DeniedToast />
      </Suspense>
    </div>
  );
}
