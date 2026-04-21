import { Link, useParams } from 'react-router-dom';
import { useLineage } from '../hooks';
import { ErrorBox, Loading, Section } from '../components/ui';
import LineageList from '../features/lineage/LineageList';

export default function WorkspaceLineagePage() {
  const { model = '', session = '' } = useParams();
  const q = useLineage(model, session);
  return (
    <>
      <div className="mb-4">
        <Link
          to={`/w/${encodeURIComponent(model)}/${encodeURIComponent(session)}`}
          className="text-sm text-blue-700 hover:underline"
        >
          ← {session}
        </Link>
      </div>
      <Section title={`Lineage · ${session}`}>
        <p className="mb-3 text-xs text-neutral-500">
          Ancestors (above) are walked parent-ward via the archive manifest. Descendants
          (below) are enumerated by scanning sessions whose parent points at this
          workspace. List mode; graph view is deferred post-MVP.
        </p>
        {q.isPending && <Loading />}
        {q.error && <ErrorBox error={q.error} />}
        {q.data && <LineageList data={q.data} />}
      </Section>
    </>
  );
}
