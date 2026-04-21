// Canonical integrity/grade badge. Backed by the Grade component in components/ui.tsx;
// this file exists so callers import a spec-named component from features/workspace/.
import { Grade } from '../../components/ui';

export default function StatusPill({ grade }: { grade: string }) {
  return <Grade grade={grade} />;
}
