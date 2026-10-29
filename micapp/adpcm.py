"""IMA ADPCM 解码，与固件 main/audio_ble.cc 的编码器逐位对齐。

帧格式（166 字节）：seq_lo seq_hi flags pred_lo pred_hi index + 160 字节 ADPCM。
固件每 20 ms 重置一次预测器与步长（丢一包只坏 20 ms），所以这里也按帧独立解码，
不保留跨帧状态。
"""
import struct

STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253,
    279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166,
    1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428,
    4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289,
    16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)

HEADER = 6
SAMPLES = 320


def decode(packet: bytes):
    """返回 (seq, flags, pcm_bytes)；长度不足时返回 None。"""
    if len(packet) < HEADER:
        return None
    seq = packet[0] | (packet[1] << 8)
    flags = packet[2]
    pred = struct.unpack_from("<h", packet, 3)[0]
    index = packet[5]
    if index > 88:
        index = 88

    out = [0] * SAMPLES
    out[0] = pred
    data = packet[HEADER:]
    for n in range(SAMPLES - 1):
        byte = data[n >> 1]
        nib = (byte >> 4) if (n & 1) == 0 else (byte & 0x0F)
        step = STEP_TABLE[index]
        diff = step >> 3
        if nib & 4:
            diff += step
        if nib & 2:
            diff += step >> 1
        if nib & 1:
            diff += step >> 2
        pred += -diff if (nib & 8) else diff
        if pred > 32767:
            pred = 32767
        elif pred < -32768:
            pred = -32768
        index += INDEX_TABLE[nib]
        if index < 0:
            index = 0
        elif index > 88:
            index = 88
        out[n + 1] = pred

    return seq, flags, struct.pack("<%dh" % SAMPLES, *out)
