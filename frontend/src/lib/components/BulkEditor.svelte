<script lang="ts">
	import { untrack } from 'svelte';
	import { Plus, Trash2, Sparkles, Wand2, ArrowLeft, Loader } from '@lucide/svelte';
	import Modal from './Modal.svelte';
	import DiffTable from './DiffTable.svelte';
	import { post } from '../api';
	import { FIELDS, FIELD_LABELS } from '../format';
	import { errorToast, toast } from '../state.svelte';
	import type { Proposal } from '../types';

	let {
		open = $bindable(false),
		itemIds,
		mode = 'ops',
		onapplied
	}: { open?: boolean; itemIds: number[]; mode?: 'ops' | 'ai'; onapplied?: () => void } = $props();

	type Op = Record<string, any> & { op: string };
	const OP_TYPES: { op: string; label: string; make: () => Op }[] = [
		{ op: 'set', label: 'Set field', make: () => ({ op: 'set', field: 'album', value: '' }) },
		{ op: 'replace', label: 'Find & replace', make: () => ({ op: 'replace', field: 'title', find: '', replace: '', regex: false, case_sensitive: false }) },
		{ op: 'parse', label: 'Split text into fields', make: () => ({ op: 'parse', source: 'yt_title', pattern: '{artist} - {title}' }) },
		{ op: 'clean', label: 'Remove junk like "(Official Video)"', make: () => ({ op: 'clean', field: 'title' }) },
		{ op: 'case', label: 'Change case', make: () => ({ op: 'case', field: 'title', mode: 'title' }) },
		{ op: 'number', label: 'Number tracks in order', make: () => ({ op: 'number', start: 1, total: true }) },
		{ op: 'copy', label: 'Copy value', make: () => ({ op: 'copy', from: 'channel', to: 'album_artist' }) },
		{ op: 'clear', label: 'Clear field', make: () => ({ op: 'clear', field: 'comment' }) },
		{ op: 'trim', label: 'Trim extra spaces', make: () => ({ op: 'trim' }) },
		{ op: 'rename', label: 'Rename files', make: () => ({ op: 'rename', template: '{artist} - {title}' }) }
	];
	const SOURCES = [...FIELDS, 'yt_title', 'channel', 'playlist', 'yt_artist', 'yt_album', 'upload_year', 'position', 'id'];
	const SOURCE_LABEL = (s: string) =>
		FIELD_LABELS[s] ?? ({ yt_title: 'YouTube title', channel: 'Channel', playlist: 'Playlist name', yt_artist: 'YouTube Music artist', yt_album: 'YouTube Music album', upload_year: 'Upload year', position: 'Playlist position', id: 'Video id' } as Record<string, string>)[s] ?? s;

	const PRESETS: { label: string; ops: Op[] }[] = [
		{ label: 'Split "Artist - Title" from YouTube title', ops: [{ op: 'parse', source: 'yt_title', pattern: '{artist} - {title}' }, { op: 'clean', field: 'title' }, { op: 'trim' }] },
		{ label: 'Album = playlist name', ops: [{ op: 'set', field: 'album', value: '{playlist}' }, { op: 'set', field: 'album_artist', value: 'Various Artists' }] },
		{ label: 'Number by playlist order', ops: [{ op: 'number', start: 1, total: true }] },
		{ label: 'Clean titles', ops: [{ op: 'clean', field: 'title' }, { op: 'trim' }] },
		{ label: 'Year from upload date', ops: [{ op: 'set', field: 'year', value: '{upload_year}' }] }
	];
	const AI_PRESETS = [
		'Split artist and title, remove noise like (Official Video) or [Lyrics], use the real artist name instead of VEVO or "- Topic" channels.',
		'Fill in the real album name and release year where you are sure.',
		'Set a fitting genre for every track (one genre, common names like Pop, Hip-Hop, Electronic, Rock).',
		'Fix capitalization of titles and artists, keep stylized names (e.g. "deadmau5") as they are officially written.'
	];

	let tab = $state<'ops' | 'ai'>('ops');
	let ops = $state<Op[]>([]);
	let proposals = $state<Proposal[] | null>(null);
	let busy = $state(false);
	let instruction = $state(AI_PRESETS[0]);
	let aiFields = $state<string[]>(['title', 'artist', 'album', 'album_artist', 'year', 'genre']);
	let model = $state('');
	let aiErrors = $state<string[]>([]);
	let addKind = $state('set');

	$effect(() => {
		if (open) {
			const m = mode;
			untrack(() => {
				tab = m;
				proposals = null;
				aiErrors = [];
			});
		}
	});

	function addOp() {
		const t = OP_TYPES.find((o) => o.op === addKind);
		if (t) ops.push(t.make());
	}

	async function preview() {
		if (!ops.length) return toast('Add at least one operation');
		busy = true;
		try {
			const r = await post<{ proposals: Proposal[] }>('/metadata/preview', { item_ids: itemIds, ops: $state.snapshot(ops) });
			proposals = r.proposals;
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}

	async function askAi() {
		busy = true;
		aiErrors = [];
		try {
			const r = await post<{ proposals: Proposal[]; errors: string[] }>('/metadata/ai', {
				item_ids: itemIds,
				instruction,
				fields: aiFields,
				model: model || undefined
			});
			proposals = r.proposals;
			aiErrors = r.errors;
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}

	async function apply() {
		if (!proposals) return;
		const changes = proposals
			.filter((p) => p.include !== false && (p.changed.length || p.rename_to))
			.map((p) => ({
				item_id: p.item_id,
				fields: Object.fromEntries(p.changed.map((f) => [f, p.after[f] ?? ''])),
				rename_to: p.rename_to || undefined
			}));
		if (!changes.length) return toast('Nothing selected');
		busy = true;
		try {
			const r = await post<{ results: { ok: boolean; error?: string }[] }>('/metadata/apply', { changes });
			const bad = r.results.filter((x) => !x.ok);
			if (bad.length) toast(`${r.results.length - bad.length} saved, ${bad.length} failed: ${bad[0].error}`, 'error');
			else toast(`Saved tags of ${r.results.length} tracks`, 'ok');
			open = false;
			onapplied?.();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:open title="Edit metadata of {itemIds.length} track{itemIds.length === 1 ? '' : 's'}" width="860px">
	{#if proposals}
		{#if aiErrors.length}<div class="pill err" style="margin-bottom: 8px">{aiErrors.length} AI batch(es) failed: {aiErrors[0]}</div>{/if}
		<DiffTable bind:proposals />
	{:else}
		<div class="tabs">
			<button class:active={tab === 'ops'} onclick={() => (tab = 'ops')}><Wand2 size={15} /> Rules</button>
			<button class:active={tab === 'ai'} onclick={() => (tab = 'ai')}><Sparkles size={15} /> AI (OpenRouter)</button>
		</div>

		{#if tab === 'ops'}
			<div class="row wrap presets">
				<span class="faint small">Presets:</span>
				{#each PRESETS as p (p.label)}
					<button class="sm" onclick={() => (ops = [...ops, ...structuredClone(p.ops)])}>{p.label}</button>
				{/each}
			</div>
			<div class="ops">
				{#each ops as op, i (i)}
					<div class="op">
						<span class="num">{i + 1}</span>
						<div class="grow opbody">
							<b class="small">{OP_TYPES.find((t) => t.op === op.op)?.label}</b>
							<div class="row wrap">
								{#if ['set', 'replace', 'clean', 'case', 'clear'].includes(op.op)}
									<select bind:value={op.field} style="width: 150px">
										{#each FIELDS as f (f)}<option value={f}>{FIELD_LABELS[f]}</option>{/each}
									</select>
								{/if}
								{#if op.op === 'set'}
									<input class="grow" bind:value={op.value} placeholder="Text or placeholders like {'{playlist}'}, {'{yt_title}'}, {'{channel}'}" />
								{:else if op.op === 'replace'}
									<input class="grow" bind:value={op.find} placeholder="Find" />
									<input class="grow" bind:value={op.replace} placeholder="Replace with" />
									<label class="check small"><input type="checkbox" bind:checked={op.regex} /> regex</label>
									<label class="check small"><input type="checkbox" bind:checked={op.case_sensitive} /> match case</label>
								{:else if op.op === 'parse'}
									<select bind:value={op.source} style="width: 170px">
										{#each SOURCES as s (s)}<option value={s}>{SOURCE_LABEL(s)}</option>{/each}
									</select>
									<input class="grow mono" bind:value={op.pattern} placeholder={'{artist} - {title}'} />
								{:else if op.op === 'case'}
									<select bind:value={op.mode} style="width: 150px">
										<option value="title">Title Case</option>
										<option value="sentence">Sentence case</option>
										<option value="upper">UPPER</option>
										<option value="lower">lower</option>
									</select>
								{:else if op.op === 'number'}
									<label class="row small">start at <input type="number" bind:value={op.start} style="width: 80px" /></label>
									<label class="check small"><input type="checkbox" bind:checked={op.total} /> also set track total</label>
								{:else if op.op === 'copy'}
									<select bind:value={op.from} style="width: 170px">
										{#each SOURCES as s (s)}<option value={s}>{SOURCE_LABEL(s)}</option>{/each}
									</select>
									<span class="faint">→</span>
									<select bind:value={op.to} style="width: 150px">
										{#each FIELDS as f (f)}<option value={f}>{FIELD_LABELS[f]}</option>{/each}
									</select>
								{:else if op.op === 'rename'}
									<input class="grow mono" bind:value={op.template} placeholder={'{artist} - {title}'} />
								{/if}
							</div>
							{#if op.op === 'parse'}<span class="hint">Use {'{field}'} for fields, {'{_}'} to skip a part. Example: {'{artist} - {title} ({_})'}</span>{/if}
							{#if op.op === 'rename'}<span class="hint">The extension stays. Placeholders: any field plus {'{playlist}'}, {'{position}'}, {'{id}'}</span>{/if}
						</div>
						<button class="ghost icon sm" onclick={() => ops.splice(i, 1)} title="Remove"><Trash2 size={15} /></button>
					</div>
				{:else}
					<div class="empty small">Pick a preset or add rules below. They run top to bottom, nothing is written before you confirm the preview.</div>
				{/each}
			</div>
			<div class="row">
				<select bind:value={addKind} style="max-width: 280px">
					{#each OP_TYPES as t (t.op)}<option value={t.op}>{t.label}</option>{/each}
				</select>
				<button onclick={addOp}><Plus size={15} /> Add rule</button>
				<span class="spacer"></span>
				{#if ops.length}<button class="ghost sm" onclick={() => (ops = [])}>Clear all</button>{/if}
			</div>
		{:else}
			<div class="col">
				<label class="field">
					What should the AI do?
					<textarea rows="3" bind:value={instruction}></textarea>
				</label>
				<div class="row wrap">
					{#each AI_PRESETS as p (p)}
						<button class="sm" onclick={() => (instruction = p)} title={p}>{p.split(',')[0].slice(0, 42)}…</button>
					{/each}
				</div>
				<div class="field">
					<span>Fields the AI may change</span>
					<div class="row wrap">
						{#each FIELDS.filter((f) => f !== 'lyrics') as f (f)}
							<label class="check small">
								<input type="checkbox" checked={aiFields.includes(f)} onchange={(e) => (aiFields = e.currentTarget.checked ? [...aiFields, f] : aiFields.filter((x) => x !== f))} />
								{FIELD_LABELS[f]}
							</label>
						{/each}
					</div>
				</div>
				<label class="field">
					Model (optional, default from settings)
					<input class="mono" bind:value={model} placeholder="e.g. anthropic/claude-haiku-5.5" />
				</label>
				<p class="hint">
					Sends YouTube title, channel, a short description excerpt and the current tags of the selected tracks to OpenRouter. You review every suggestion before it is written.
				</p>
			</div>
		{/if}
	{/if}

	{#snippet footer()}
		{#if proposals}
			<button onclick={() => (proposals = null)}><ArrowLeft size={15} /> Back</button>
			<span class="spacer"></span>
			<button class="primary" onclick={apply} disabled={busy}>
				{#if busy}<Loader size={15} class="spin" />{/if}
				Write tags
			</button>
		{:else}
			<button onclick={() => (open = false)}>Cancel</button>
			{#if tab === 'ops'}
				<button class="primary" onclick={preview} disabled={busy || !ops.length}>
					{#if busy}<Loader size={15} class="spin" />{/if} Preview changes
				</button>
			{:else}
				<button class="primary" onclick={askAi} disabled={busy || !aiFields.length}>
					{#if busy}<Loader size={15} class="spin" /> Asking…{:else}<Sparkles size={15} /> Get suggestions{/if}
				</button>
			{/if}
		{/if}
	{/snippet}
</Modal>

<style>
	.tabs {
		display: flex;
		gap: 4px;
		margin-bottom: 12px;
		border-bottom: 1px solid var(--line);
	}
	.tabs button {
		border: none;
		background: none;
		border-radius: 0;
		border-bottom: 2px solid transparent;
		color: var(--text-2);
		margin-bottom: -1px;
	}
	.tabs button.active {
		color: var(--text);
		border-bottom-color: var(--accent);
	}
	.presets {
		margin-bottom: 10px;
	}
	.ops {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin-bottom: 10px;
	}
	.op {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 10px;
		background: var(--bg);
	}
	.opbody {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.num {
		width: 22px;
		height: 22px;
		border-radius: 50%;
		background: var(--accent-soft);
		color: var(--accent);
		font-size: 12px;
		font-weight: 700;
		display: grid;
		place-items: center;
		flex-shrink: 0;
	}
</style>
