import config from './vite.config.js'

export default {
  ...config,
  plugins: [...config.plugins, {
    name: 'voice-smoke-fixture',
    transformIndexHtml: {
      order: 'pre',
      handler() {
        return [{
          tag: 'script', attrs: { type: 'module', src: '/fixtures/voice.js' }, injectTo: 'head-prepend',
        }]
      },
    },
  }],
}
