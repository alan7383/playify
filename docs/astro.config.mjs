// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
// Deploy trigger
export default defineConfig({
	site: 'https://alan7383.github.io',
	base: '/playify',
	integrations: [
		starlight({
			title: 'Playify',
			customCss: ['./src/custom.css'],
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/alan7383/playify' }],
			sidebar: [
				{
					label: 'Getting Started',
					items: [
						{ label: 'Introduction', slug: 'getting-started/introduction' },
						{ label: 'Installation', slug: 'getting-started/installation' },
						{ label: 'Inviting the Bot', slug: 'getting-started/inviting-the-bot' },
					],
				},
				{
					label: 'Configuration & Admin',
					items: [
						{ label: 'Server Setup', slug: 'configuration/server-setup' },
						{ label: 'TUI Dashboard', slug: 'configuration/tui-dashboard' },
						{ label: 'YouTube Cookies', slug: 'configuration/youtube-cookies' },
					],
				},
				{
					label: 'Playback & Features',
					items: [
						{ label: 'Commands Reference', slug: 'playback-and-features/commands-reference' },
						{ label: '24/7 & Autoplay', slug: 'playback-and-features/24-7-and-autoplay' },
						{ label: 'Lyrics & Karaoke', slug: 'playback-and-features/lyrics-and-karaoke' },
					],
				},
				{
					label: 'Troubleshooting',
					items: [
						{ label: 'FAQ', slug: 'troubleshooting/faq' },
						{ label: 'Backups & Migrations', slug: 'troubleshooting/backups-migrations' },
					],
				},
			],
		}),
	],
});
