<script lang="ts">
	import { SCHEDULES } from '../format';
	let { value = $bindable('') }: { value?: string } = $props();
	let custom = $state(false);
	$effect.pre(() => {
		if (value && !SCHEDULES.some((s) => s.value === value)) custom = true;
	});
	function pick(e: Event) {
		const v = (e.target as HTMLSelectElement).value;
		if (v === '__custom') {
			custom = true;
			if (!value) value = '0 */6 * * *';
		} else {
			custom = false;
			value = v;
		}
	}
</script>

<div class="row">
	<select onchange={pick} value={custom ? '__custom' : value}>
		{#each SCHEDULES as s (s.value)}<option value={s.value}>{s.label}</option>{/each}
		<option value="__custom">Custom cron…</option>
	</select>
	{#if custom}
		<input class="mono" style="max-width: 170px" bind:value placeholder="m h dom mon dow" />
	{/if}
</div>
