import { ShieldCheck } from 'lucide-react';
import type { Role } from '../types';

const ROLE_LABELS: Record<Role, string> = {
  admin: 'Admin',
  finance: 'Finance',
  engineering: 'Engineering',
  guest: 'Guest',
};

interface RoleSelectorProps {
  role: Role;
  disabled?: boolean;
  onChange: (role: Role) => void;
}

export default function RoleSelector({ role, disabled = false, onChange }: RoleSelectorProps) {
  return (
    <label className="role-control">
      <span className="role-control-icon"><ShieldCheck size={15} /></span>
      <span className="sr-only">Demo access role</span>
      <select
        aria-label="Demo access role"
        value={role}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as Role)}
      >
        {(Object.keys(ROLE_LABELS) as Role[]).map((item) => (
          <option key={item} value={item}>{ROLE_LABELS[item]}</option>
        ))}
      </select>
      <span className="role-control-caption">DEMO ROLE</span>
    </label>
  );
}
