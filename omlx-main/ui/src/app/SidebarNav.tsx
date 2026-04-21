import { NavLink } from 'react-router-dom';

const items = [
  { to: '/', label: 'Workspaces', end: true },
  { to: '/transfers', label: 'Transfers' },
  { to: '/maintenance', label: 'Maintenance' },
  { to: '/settings', label: 'Settings' },
] as const;

export default function SidebarNav() {
  return (
    <nav className="flex flex-col gap-1 p-3">
      <div className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wider text-neutral-500">
        Operator
      </div>
      {items.map((i) => (
        <NavLink
          key={i.to}
          to={i.to}
          end={'end' in i ? i.end : undefined}
          className={({ isActive }) =>
            `rounded px-3 py-1.5 text-sm font-medium ${
              isActive
                ? 'bg-blue-600 text-white'
                : 'text-neutral-700 hover:bg-neutral-200'
            }`
          }
        >
          {i.label}
        </NavLink>
      ))}
      <div className="mt-4 px-2 text-[11px] uppercase tracking-wider text-neutral-400">
        Read-only
      </div>
      <NavLink
        to="/diff"
        className={({ isActive }) =>
          `rounded px-3 py-1.5 text-sm ${
            isActive
              ? 'bg-neutral-200 text-neutral-900'
              : 'text-neutral-600 hover:bg-neutral-100'
          }`
        }
      >
        Arbitrary diff
      </NavLink>
    </nav>
  );
}
