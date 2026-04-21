import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useForkWorkspace } from '../hooks';
import { ErrorBox, Section } from '../components/ui';

export default function ForkPage() {
  const params = useParams();
  const navigate = useNavigate();
  const model = params.model!;
  const session = params.session!;

  const [form, setForm] = useState({
    dst_session_id: '',
    branch_reason: '',
    at_turn: '',
    label: '',
    description: '',
    task_tag: '',
  });

  const m = useForkWorkspace(model, session);

  const canSubmit =
    form.dst_session_id.trim().length > 0 && form.branch_reason.trim().length >= 4;

  const submit = () =>
    m.mutate(
      {
        dst_session_id: form.dst_session_id,
        branch_reason: form.branch_reason,
        at_turn: form.at_turn || null,
        label: form.label || null,
        description: form.description || null,
        task_tag: form.task_tag || null,
      },
      {
        onSuccess: (data) => {
          navigate(
            `/w/${encodeURIComponent(data.model_name)}/${encodeURIComponent(data.session_id)}`,
          );
        },
      },
    );

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
      <Section title={`Fork ${session}`}>
        <div className="card space-y-3">
          <p className="text-sm text-neutral-600">
            Forking creates a new workspace starting from an existing turn. A branch reason of
            at least 4 characters is required.
          </p>
          {(
            [
              ['dst_session_id', 'New session id *'],
              ['branch_reason', 'Branch reason * (min 4 chars)'],
              ['at_turn', 'At turn (blank = head)'],
              ['label', 'Label'],
              ['description', 'Description'],
              ['task_tag', 'Task tag'],
            ] as const
          ).map(([k, label]) => (
            <div key={k}>
              <label className="label">{label}</label>
              <input
                className="input"
                value={(form as Record<string, string>)[k]}
                onChange={(e) => setForm({ ...form, [k]: e.target.value })}
              />
            </div>
          ))}
          <div className="flex gap-2">
            <button
              className="btn-primary"
              disabled={!canSubmit || m.isPending}
              onClick={submit}
            >
              Fork
            </button>
          </div>
          {m.error && <ErrorBox error={m.error} />}
        </div>
      </Section>
    </>
  );
}
