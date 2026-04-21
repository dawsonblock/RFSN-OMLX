import { useState } from 'react';
import {
  useBundles,
  useExportWorkspace,
  useInspectBundle,
  useImportBundle,
  usePinBundle,
} from '../hooks';
import { ErrorBox, Section, Empty, formatBytes, formatTs } from '../components/ui';
import BundleProvenanceCard from '../features/transfers/BundleProvenanceCard';
import ConflictPolicyDialog from '../features/transfers/ConflictPolicyDialog';

export default function Transfers() {
  return (
    <>
      <ExportCard />
      <ImportCard />
      <BundlesSection />
    </>
  );
}

function BundlesSection() {
  const bundles = useBundles();
  return (
    <Section title="Bundles in ui_exports/">
      {bundles.isPending && <div>Loading…</div>}
      {bundles.error && <ErrorBox error={bundles.error} />}
      {bundles.data && bundles.data.length === 0 && (
        <Empty>No bundles exported yet.</Empty>
      )}
      {bundles.data && bundles.data.length > 0 && (
        <div className="overflow-x-auto rounded border border-neutral-200 bg-white">
          <table className="min-w-full divide-y divide-neutral-200 text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-500">
              <tr>
                <th className="px-3 py-2">Path</th>
                <th className="px-3 py-2">Size</th>
                <th className="px-3 py-2">Modified</th>
                <th className="px-3 py-2">Pinned</th>
                <th className="px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {bundles.data.map((b) => {
                const name = b.path.split('/').pop() ?? b.path;
                return (
                  <tr key={b.path}>
                    <td className="px-3 py-2 font-mono text-xs">{b.path}</td>
                    <td className="px-3 py-2">{formatBytes(b.size_bytes)}</td>
                    <td className="px-3 py-2 text-xs">{formatTs(b.mtime)}</td>
                    <td className="px-3 py-2">{b.pinned ? '📌' : '—'}</td>
                    <td className="px-3 py-2">
                      <PinAction name={name} pinned={b.pinned} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function PinAction({ name, pinned }: { name: string; pinned: boolean }) {
  const m = usePinBundle();
  return (
    <button
      className="btn"
      disabled={m.isPending}
      onClick={() => m.mutate({ filename: name, pinned: !pinned })}
    >
      {pinned ? 'Unpin' : 'Pin'}
    </button>
  );
}

function ExportCard() {
  const [form, setForm] = useState({
    model_name: '',
    session_id: '',
    out_filename: '',
    allow_missing_blocks: false,
  });
  const m = useExportWorkspace();
  return (
    <Section title="Export">
      <div className="card space-y-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Input label="Model" value={form.model_name} onChange={(v) => setForm({ ...form, model_name: v })} />
          <Input label="Session" value={form.session_id} onChange={(v) => setForm({ ...form, session_id: v })} />
          <Input
            label="Output filename (opt)"
            value={form.out_filename}
            onChange={(v) => setForm({ ...form, out_filename: v })}
          />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.allow_missing_blocks}
            onChange={(e) => setForm({ ...form, allow_missing_blocks: e.target.checked })}
          />
          Allow missing blocks (partially exportable)
        </label>
        <div>
          <button
            className="btn-primary"
            disabled={!form.model_name || !form.session_id || m.isPending}
            onClick={() =>
              m.mutate({
                model_name: form.model_name,
                session_id: form.session_id,
                out_filename: form.out_filename || null,
                allow_missing_blocks: form.allow_missing_blocks,
              })
            }
          >
            Export
          </button>
        </div>
        {m.error && <ErrorBox error={m.error} />}
        {m.data && (
          <div className="rounded border border-green-200 bg-green-50 p-2 text-sm">
            Exported {m.data.block_count} blocks (missing: {m.data.missing_block_count}) to{' '}
            <span className="font-mono">{m.data.path}</span>
          </div>
        )}
      </div>
    </Section>
  );
}

function ImportCard() {
  const [form, setForm] = useState({
    bundle_filename: '',
    conflict_policy: 'fail' as 'fail' | 'rename' | 'overwrite',
    re_root_lineage: false,
    expected_model_name: '',
    expected_block_size: '',
  });
  const [confirmOpen, setConfirmOpen] = useState(false);
  const inspect = useInspectBundle();
  const imp = useImportBundle();
  const canInspect = Boolean(form.bundle_filename);
  const canImport = canInspect && inspect.data != null;

  const submitImport = () => {
    imp.mutate({
      bundle_filename: form.bundle_filename,
      conflict_policy: form.conflict_policy,
      re_root_lineage: form.re_root_lineage,
      expected_model_name: form.expected_model_name || null,
      expected_block_size: form.expected_block_size
        ? Number(form.expected_block_size)
        : null,
    });
    setConfirmOpen(false);
  };

  const inspectedSession = (inspect.data?.envelope?.source_session_id as string | undefined) ?? form.bundle_filename;

  return (
    <Section title="Import (drop bundle into ui_imports/)">
      <div className="card space-y-3">
        <Input
          label="Bundle filename"
          value={form.bundle_filename}
          onChange={(v) => setForm({ ...form, bundle_filename: v })}
        />
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <div>
            <label className="label">Conflict policy</label>
            <select
              className="input"
              value={form.conflict_policy}
              onChange={(e) =>
                setForm({
                  ...form,
                  conflict_policy: e.target.value as typeof form.conflict_policy,
                })
              }
            >
              <option value="fail">fail (default — safest)</option>
              <option value="rename">rename</option>
              <option value="overwrite">overwrite</option>
            </select>
          </div>
          <Input
            label="Expected model (opt)"
            value={form.expected_model_name}
            onChange={(v) => setForm({ ...form, expected_model_name: v })}
          />
          <Input
            label="Expected block size (opt)"
            value={form.expected_block_size}
            onChange={(v) => setForm({ ...form, expected_block_size: v })}
          />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.re_root_lineage}
            onChange={(e) => setForm({ ...form, re_root_lineage: e.target.checked })}
          />
          Re-root lineage (drop ancestry link)
        </label>
        <div className="flex gap-2">
          <button
            className="btn"
            disabled={!canInspect || inspect.isPending}
            onClick={() => inspect.mutate(form.bundle_filename)}
          >
            Inspect
          </button>
          <button
            className="btn-primary"
            disabled={!canImport || imp.isPending}
            onClick={() => setConfirmOpen(true)}
            title={!canImport ? 'Inspect the bundle first' : undefined}
          >
            Import
          </button>
        </div>
        {inspect.error && <ErrorBox error={inspect.error} />}
        {inspect.data && <BundleProvenanceCard bundle={inspect.data} />}
        {imp.error && <ErrorBox error={imp.error} />}
        {imp.data && (
          <div className="rounded border border-green-200 bg-green-50 p-2 text-sm">
            Imported {imp.data.model_name}/{imp.data.session_id} (
            {imp.data.blocks_written} blocks, {imp.data.conflict_policy})
          </div>
        )}
      </div>

      <ConflictPolicyDialog
        open={confirmOpen}
        policy={form.conflict_policy}
        session_id={inspectedSession}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={submitImport}
      />
    </Section>
  );
}

function Input({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <input className="input" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
