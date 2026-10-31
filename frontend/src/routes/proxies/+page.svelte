<script lang="ts">
	import { Plus, Activity, Trash2, Loader, RotateCcw } from '@lucide/svelte';
	import { del, get, patch, post, put } from '#lib/api.ts';
	import { ago, datetime } from '#lib/format.ts';
	import { errorToast, on, toast } from '#lib/state.svelte.ts';
	import Modal from '#lib/components/Modal.svelte';

	let proxies = $state<any[]>([]);
	let s = $state<any>(null);
	let open = $state(false);
	let text = $state('');
	let label = $state('');
	let testing = $state<Set<number>>(new Set());

	async function load() {
		try {
			[proxies, s] = await Promise.all([get('/proxies'), get('/settings')]);
		} catch (e) {
			errorToast(e);
		}
	}
	load();
	$effect(() => on('proxy', () => load()));

	async function add() {
		const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
		try {
			const r = await post('/proxies', lines.length === 1 ? { url: lines[0], label } : { text });
			toast(`Added ${r.created.length} prox${r.created.length === 1 ? 'y' : 'ies'}`, 'ok');
			open = false;
			text = '';
			label = '';
			await load();
			r.created.forEach((id: number) => test(id));
		} catch (e) {
			errorToast(e);
		}
	}
	async function test(id: number) {
		testing = new Set([...testing, id]);
		try {
			const r = await post(`/proxies/${id}/test`);
			if (!r.ok) toast(`Proxy #${id}: ${r.error}`, 'error');
		} catch (e) {
			errorToast(e);
		} finally {
			testing.delete(id);
			testing = new Set(testing);
			load();
		}
	}
	async function testAll() {
		await Promise.all(proxies.filter((p) => p.enabled).map((p) => test(p.id)));
	}
	async function toggle(p: any) {
		await patch(`/proxies/${p.id}`, { enabled: !p.enabled }).catch(errorToast);
		load();
	}
	async function reset(p: any) {
		await post(`/proxies/${p.id}/reset`).catch(errorToast);
		load();
	}
	async function remove(p: any) {
		if (!confirm(`Delete proxy ${p.label}?`)) return;
		await del(`/proxies/${p.id}`).catch(errorToast);
		load();
	}
	async function saveSettings() {
		try {
			s = await put('/settings', {
				proxy_strategy: s.proxy_strategy,
				proxy_cooldown_minutes: Number(s.proxy_cooldown_minutes),
				proxy_max_consecutive_fails: Number(s.proxy_max_consecutive_fails),
				proxy_check_hours: Number(s.proxy_check_hours)
			});
			toast('Saved', 'ok');
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="page-head">
	<div class="col" style="gap: 2px">
		<h1>Proxies</h1>
		<p class="muted small">Spread downloads over several IPs. A proxy that gets blocked (HTTP 429, bot check) cools down and the next one takes over.</p>
	</div>
	<span class="spacer"></span>
	<button onclick={testAll} disabled={!proxies.length}><Activity size={15} /> Test all</button>
	<button class="primary" onclick={() => (open = true)}><Plus size={15} /> Add proxies</button>
</div>

{#if s}
	<section class="card pad" style="margin-bottom: 16px">
		<div class="grid4">
			<label class="field">
				Strategy
				<select bind:value={s.proxy_strategy}>
					<option value="none">Off (direct, unless a playlist says otherwise)</option>
					<option value="round_robin">Round robin</option>
					<option value="random">Random</option>
				</select>
			</label>
			<label class="field">Cooldown after a block (min)<input type="number" min="1" bind:value={s.proxy_cooldown_minutes} /></label>
			<label class="field">Disable after N fails in a row<input type="number" min="0" bind:value={s.proxy_max_consecutive_fails} /></label>
			<label class="field">Health check every (h, 0 = off)<input type="number" min="0" bind:value={s.proxy_check_hours} /></label>
		</div>
		<div class="row" style="margin-top: 12px"><span class="hint grow">Cooldown doubles with every block in a row (max 24 h).</span><button onclick={saveSettings}>Save</button></div>
	</section>
{/if}

<div class="card table-wrap">
	<table class="tbl">
		<thead><tr><th>Proxy</th><th>State</th><th class="hide-sm">Latency</th><th class="hide-sm">Success / fail</th><th class="hide-sm">Last OK</th><th></th></tr></thead>
		<tbody>
			{#each proxies as p (p.id)}
				<tr class:off={!p.enabled}>
					<td style="max-width: 320px">
						<b>{p.label}</b> <span class="faint small">#{p.id}</span>
						<div class="mono small muted ellipsis">{p.url}</div>
						{#if p.last_error}<div class="small errtext ellipsis" title={p.last_error}>{p.last_error}</div>{/if}
					</td>
					<td>
						{#if testing.has(p.id)}<span class="pill info"><Loader size={11} class="spin" /> testing</span>
						{:else if !p.enabled}<span class="pill">disabled</span>
						{:else if p.cooling_down}<span class="pill warn" title="until {datetime(p.cooldown_until)}">cooling down</span>
						{:else if p.last_ok_at}<span class="pill ok">ok</span>
						{:else}<span class="pill">untested</span>{/if}
					</td>
					<td class="hide-sm small">{p.last_latency_ms ? `${p.last_latency_ms} ms` : ''}</td>
					<td class="hide-sm small">{p.success_count} / {p.fail_count}</td>
					<td class="hide-sm small muted">{ago(p.last_ok_at)}</td>
					<td>
						<div class="row" style="justify-content: flex-end">
							<button class="sm" onclick={() => test(p.id)} disabled={testing.has(p.id)}>Test</button>
							{#if p.cooling_down || p.consecutive_fails}<button class="sm icon" onclick={() => reset(p)} title="Clear cooldown"><RotateCcw size={14} /></button>{/if}
							<button class="sm" onclick={() => toggle(p)}>{p.enabled ? 'Disable' : 'Enable'}</button>
							<button class="sm icon ghost danger" onclick={() => remove(p)} title="Delete"><Trash2 size={14} /></button>
						</div>
					</td>
				</tr>
			{:else}
				<tr><td colspan="6" class="empty">No proxies. Downloads use the server's own connection.</td></tr>
			{/each}
		</tbody>
	</table>
</div>

<Modal bind:open title="Add proxies" width="560px">
	<div class="col" style="gap: 12px">
		<label class="field">
			Proxy URLs, one per line
			<textarea class="mono" rows="6" bind:value={text} placeholder={'http://user:pass@1.2.3.4:8080\nsocks5://5.6.7.8:1080'}></textarea>
			<span class="hint">http, https, socks4, socks5 and socks5h are supported. Credentials are stored encrypted.</span>
		</label>
		{#if text.split('\n').filter((l) => l.trim()).length === 1}
			<label class="field">Name (optional)<input bind:value={label} /></label>
		{/if}
	</div>
	{#snippet footer()}
		<button onclick={() => (open = false)}>Cancel</button>
		<button class="primary" onclick={add} disabled={!text.trim()}>Add and test</button>
	{/snippet}
</Modal>

<style>
	.grid4 {
		display: grid;
		grid-template-columns: 1.6fr 1fr 1fr 1fr;
		gap: 12px;
	}
	tr.off {
		opacity: 0.55;
	}
	.errtext {
		color: var(--err);
	}
	@media (max-width: 860px) {
		.grid4 {
			grid-template-columns: 1fr 1fr;
		}
	}
	@media (max-width: 720px) {
		.hide-sm {
			display: none;
		}
	}
</style>
