<script lang="ts">
	let { provider, label = '', size = 'md' }: { provider: string | null | undefined; label?: string; size?: 'sm' | 'md' } = $props();
	const COLORS: Record<string, string> = { youtube: '#ff0033', spotify: '#1db954', apple: '#fa2d48', tidal: '#7c7c85' };
	const NAMES: Record<string, string> = { youtube: 'YouTube Music', spotify: 'Spotify', apple: 'Apple Music', tidal: 'TIDAL' };
	const LETTER: Record<string, string> = { youtube: 'Y', spotify: 'S', apple: 'A', tidal: 'T' };
</script>

<span class="pb {size}">
	<span class="dot" style:background={COLORS[provider ?? ''] ?? 'var(--text-3)'}>{LETTER[provider ?? ''] ?? '?'}</span>
	<span class="ellipsis">{label || NAMES[provider ?? ''] || provider}</span>
</span>

<style>
	.pb {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
		font-weight: 560;
	}
	.dot {
		width: 20px;
		height: 20px;
		border-radius: 6px;
		color: #fff;
		font-size: 11px;
		font-weight: 800;
		display: grid;
		place-items: center;
		flex-shrink: 0;
	}
	.sm .dot {
		width: 16px;
		height: 16px;
		border-radius: 5px;
		font-size: 9.5px;
	}
	.sm {
		font-size: 13px;
		gap: 5px;
	}
</style>
