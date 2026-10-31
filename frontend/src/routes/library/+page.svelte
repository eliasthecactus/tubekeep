<script lang="ts">
	import { get } from '#lib/api.ts';
	import ItemTable from '#lib/components/ItemTable.svelte';

	let playlists = $state<{ id: number; title: string | null; url: string }[]>([]);
	get('/playlists').then((p) => (playlists = p));
</script>

<div class="page-head">
	<div class="col" style="gap: 2px">
		<h1>Library</h1>
		<p class="muted small">All tracks of all playlists. Select tracks to edit tags in bulk, with rules or AI.</p>
	</div>
</div>
<ItemTable {playlists} />
