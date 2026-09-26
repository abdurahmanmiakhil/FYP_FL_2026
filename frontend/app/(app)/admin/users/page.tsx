"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, Search, UserPlus } from "lucide-react";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { PageHeader } from "@/components/clinical";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorMessage, type Role, type User } from "@/lib/api/client";
import { useCreateUser, useResetPassword, useUpdateUser, useUsers } from "@/lib/api/hooks";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { passwordSchema } from "@/lib/validation";

const ROLES: Role[] = ["pathologist", "urologist", "admin"];

const createSchema = z.object({
  email: z.string().trim().email("Enter a valid email"),
  full_name: z.string().trim().min(2, "Enter the full name"),
  role: z.enum(["pathologist", "urologist", "admin"]),
  hospital: z.string().trim().min(2, "Enter the hospital"),
  password: passwordSchema,
});
type CreateValues = z.infer<typeof createSchema>;

function CreateUserDialog() {
  const [open, setOpen] = useState(false);
  const create = useCreateUser();
  const { user } = useAuth();
  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      email: "",
      full_name: "",
      role: "pathologist",
      hospital: user?.hospital ?? "",
      password: "",
    },
  });
  const { errors } = form.formState;
  const submit = form.handleSubmit((v) =>
    create.mutate(v, {
      onSuccess: () => {
        toast.success(`Account created for ${v.email}. They must change the password at first sign-in.`);
        form.reset();
        setOpen(false);
      },
      onError: (e) => toast.error(errorMessage(e)),
    }),
  );
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <UserPlus aria-hidden /> New user
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create a user</DialogTitle>
          <DialogDescription>
            Give the initial password to the person securely; they are asked to change it.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} noValidate className="space-y-3">
          {(
            [
              ["full_name", "Full name", "text"],
              ["email", "Email", "email"],
              ["hospital", "Hospital", "text"],
              ["password", "Initial password", "password"],
            ] as const
          ).map(([name, label, type]) => (
            <div key={name} className="space-y-1.5">
              <Label htmlFor={`new-${name}`}>{label}</Label>
              <Input
                id={`new-${name}`}
                type={type}
                autoComplete="off"
                aria-invalid={!!errors[name]}
                {...form.register(name)}
              />
              {errors[name] && <p className="text-xs text-destructive">{errors[name]?.message}</p>}
            </div>
          ))}
          <div className="space-y-1.5">
            <Label htmlFor="new-role">Role</Label>
            <Controller
              control={form.control}
              name="role"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="new-role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLES.map((r) => (
                      <SelectItem key={r} value={r} className="capitalize">
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </div>
          <DialogFooter>
            <Button type="submit" loading={create.isPending}>
              Create user
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetPasswordDialog({ user }: { user: User }) {
  const [open, setOpen] = useState(false);
  const reset = useResetPassword();
  const form = useForm<{ password: string }>({
    resolver: zodResolver(z.object({ password: passwordSchema })),
    defaultValues: { password: "" },
  });
  const submit = form.handleSubmit(({ password }) =>
    reset.mutate(
      { id: user.id, password },
      {
        onSuccess: () => {
          toast.success(`Password reset for ${user.email}; all their sessions were signed out.`);
          form.reset();
          setOpen(false);
        },
        onError: (e) => toast.error(errorMessage(e)),
      },
    ),
  );
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" aria-label={`Reset password for ${user.full_name}`}>
          <KeyRound aria-hidden /> Reset password
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reset password</DialogTitle>
          <DialogDescription>
            {user.full_name} ({user.email}) will be signed out everywhere and must choose a new password.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} noValidate className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="reset-pw">New temporary password</Label>
            <Input id="reset-pw" type="password" autoComplete="new-password" {...form.register("password")} />
            {form.formState.errors.password && (
              <p className="text-xs text-destructive">{form.formState.errors.password.message}</p>
            )}
          </div>
          <DialogFooter>
            <Button type="submit" loading={reset.isPending}>
              Reset password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function UsersPage() {
  const [q, setQ] = useState("");
  const users = useUsers({ q: q || undefined });
  const update = useUpdateUser();
  const { user: me } = useAuth();

  const patch = (u: User, body: Parameters<typeof update.mutate>[0]["body"], msg: string) =>
    update.mutate(
      { id: u.id, body },
      { onSuccess: () => toast.success(msg), onError: (e) => toast.error(errorMessage(e)) },
    );

  return (
    <>
      <PageHeader
        title="Users"
        description="Accounts, roles and hospitals. Changes sign the user out of every session."
        actions={<CreateUserDialog />}
      />
      <div className="relative mb-4 max-w-sm">
        <Search
          className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          className="pl-9"
          placeholder="Search name, email or hospital"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search users"
        />
      </div>
      <Card>
        {users.isLoading ? (
          <Skeleton className="m-4 h-64" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Name</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Hospital</TableHead>
                <TableHead>Last sign-in</TableHead>
                <TableHead>Active</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.data?.items.map((u) => (
                <TableRow key={u.id}>
                  <TableCell>
                    <p className="font-medium">{u.full_name}</p>
                    <p className="text-xs text-muted-foreground">{u.email}</p>
                    <span className="mt-1 flex gap-1">
                      {u.totp_enabled && <Badge variant="success">2FA</Badge>}
                      {u.must_change_password && <Badge variant="warn">Must change password</Badge>}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Select
                      value={u.role}
                      disabled={u.id === me?.id}
                      onValueChange={(r) => patch(u, { role: r as Role }, `Role changed to ${r}.`)}
                    >
                      <SelectTrigger className="h-9 w-36 capitalize" aria-label={`Role of ${u.full_name}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ROLES.map((r) => (
                          <SelectItem key={r} value={r} className="capitalize">
                            {r}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <Input
                      defaultValue={u.hospital}
                      className="h-9 w-44"
                      aria-label={`Hospital of ${u.full_name}`}
                      onBlur={(e) => {
                        const v = e.target.value.trim();
                        if (v && v !== u.hospital) patch(u, { hospital: v }, "Hospital updated.");
                      }}
                    />
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDate(u.last_login_at)}</TableCell>
                  <TableCell>
                    <Switch
                      checked={u.is_active}
                      disabled={u.id === me?.id}
                      onCheckedChange={(v) =>
                        patch(
                          u,
                          { is_active: v },
                          v ? "Account activated." : "Account deactivated and signed out.",
                        )
                      }
                      aria-label={`${u.is_active ? "Deactivate" : "Activate"} ${u.full_name}`}
                    />
                  </TableCell>
                  <TableCell className="text-right">
                    <ResetPasswordDialog user={u} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
