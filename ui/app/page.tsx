import { UserMenu } from "@/components/user-menu";
import { requireUser } from "@/lib/session";
import { Inbox } from "./inbox";

export default async function Home() {
  const user = await requireUser();
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center justify-between border-b border-line bg-panel px-5 py-3">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent font-bold text-white">I</div>
          <div>
            <div className="font-semibold leading-tight">InboxPilot</div>
            <div className="text-xs text-muted">Brightline Supplies · shared inbox</div>
          </div>
        </div>
        <UserMenu user={user} />
      </header>
      <Inbox />
    </div>
  );
}
