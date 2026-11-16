function crc32(bytes) {
  let crc = 0xffffffff
  for (const byte of bytes) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0)
  }
  return (crc ^ 0xffffffff) >>> 0
}

export async function tagCapture(canvas, snapshotId) {
  const scale = Math.min(1, 1600 / canvas.width, 1000 / canvas.height)
  if (scale < 1) {
    const resized = document.createElement('canvas')
    resized.width = Math.round(canvas.width * scale)
    resized.height = Math.round(canvas.height * scale)
    resized.getContext('2d').drawImage(canvas, 0, 0, resized.width, resized.height)
    canvas = resized
  }
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
  if (!blob) throw new Error('The chart did not produce a PNG. Recapture it.')
  const png = new Uint8Array(await blob.arrayBuffer())
  const content = new TextEncoder().encode('tEXtinvest-snapshot\0' + snapshotId)
  const chunk = new Uint8Array(content.length + 8)
  const view = new DataView(chunk.buffer)
  view.setUint32(0, content.length - 4)
  chunk.set(content, 4)
  view.setUint32(chunk.length - 4, crc32(content))
  // Keep the header first and all original image data intact.
  const headerEnd = 33
  const tagged = new Blob([png.subarray(0, headerEnd), chunk, png.subarray(headerEnd)], { type: 'image/png' })
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(new Error('The chart PNG could not be read. Recapture it.'))
    reader.readAsDataURL(tagged)
  })
}
