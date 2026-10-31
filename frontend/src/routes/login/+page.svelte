<script lang="ts">
	import { goto } from '$app/navigation';
	import { get, post } from '#lib/api.ts';
	import { session } from '#lib/state.svelte.ts';

	let username = $state('');
	let password = $state('');
	let password2 = $state('');
	let error = $state('');
	let busy = $state(false);

	$effect(() => {
		if (session.ready && session.user) goto('/');
	});

	async function submit(e: Event) {
		e.preventDefault();
		error = '';
		if (session.setupNeeded && password !== password2) {
			error = 'Passwords do not match';
			return;
		}
		busy = true;
		try {
			await post(session.setupNeeded ? '/auth/setup' : '/auth/login', { username, password });
			const s = await get('/auth/state');
			session.user = s.user;
			session.setupNeeded = false;
			goto('/');
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			busy = false;
		}
	}
</script>

<div class="wrap">
	<form class="card" onsubmit={submit}>
		<div class="brand"><img src="/favicon.svg" alt="" width="40" height="40" /><h1>Tubekeep</h1></div>
		<p class="muted">{session.setupNeeded ? 'Create the first account. It is stored locally in the database.' : 'Sign in to manage your playlists.'}</p>
		<label class="field">Username<input bind:value={username} autocomplete="username" required /></label>
		<label class="field">Password<input type="password" bind:value={password} autocomplete={session.setupNeeded ? 'new-password' : 'current-password'} required minlength={session.setupNeeded ? 8 : undefined} /></label>
		{#if session.setupNeeded}
			<label class="field">Repeat password<input type="password" bind:value={password2} autocomplete="new-password" required /></label>
		{/if}
		{#if error}<div class="error">{error}</div>{/if}
		<button class="primary" type="submit" disabled={busy}>{session.setupNeeded ? 'Create account' : 'Sign in'}</button>
	</form>
</div>

<style>
	.wrap {
		min-height: 100vh;
		display: grid;
		place-items: center;
		padding: 16px;
	}
	form {
		width: min(380px, 100%);
		padding: 28px;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.error {
		color: var(--err);
		background: var(--err-soft);
		padding: 8px 10px;
		border-radius: 8px;
		font-size: 13px;
	}
	button {
		height: 38px;
	}
</style>
