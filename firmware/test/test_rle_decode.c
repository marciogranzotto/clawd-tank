#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>

/* We test the header directly */
#include "rle_sprite.h"

/* --- Test 1: basic decode ---------------------------------- */
static void test_basic_decode(void)
{
    /* RLE: 3x 0xAAAA, 2x 0xBBBB = 5 pixels */
    static const uint16_t rle[] = { 0xAAAA, 3, 0xBBBB, 2 };
    uint16_t out[5];
    rle_decode_rgb565(rle, out, 5);

    assert(out[0] == 0xAAAA);
    assert(out[1] == 0xAAAA);
    assert(out[2] == 0xAAAA);
    assert(out[3] == 0xBBBB);
    assert(out[4] == 0xBBBB);
    printf("  PASS: test_basic_decode\n");
}

/* --- Test 2: single-pixel runs ----------------------------- */
static void test_single_pixel_runs(void)
{
    static const uint16_t rle[] = { 0x1111, 1, 0x2222, 1, 0x3333, 1 };
    uint16_t out[3];
    rle_decode_rgb565(rle, out, 3);

    assert(out[0] == 0x1111);
    assert(out[1] == 0x2222);
    assert(out[2] == 0x3333);
    printf("  PASS: test_single_pixel_runs\n");
}

/* --- Test 3: decode to ARGB8888 with transparency ---------- */
static void test_decode_to_argb8888(void)
{
    /* 0x18C5 is the transparent key */
    static const uint16_t rle[] = { 0x18C5, 2, 0xF800, 1 };
    uint8_t out[3 * 4]; /* 3 pixels, 4 bytes each */
    rle_decode_argb8888(rle, out, 3, 0x18C5);

    /* Pixel 0: transparent — alpha must be 0 */
    assert(out[0 * 4 + 3] == 0x00);
    /* Pixel 1: transparent — alpha must be 0 */
    assert(out[1 * 4 + 3] == 0x00);
    /* Pixel 2: 0xF800 = pure red in RGB565 → R=0xFF, G=0, B=0, A=0xFF */
    assert(out[2 * 4 + 3] == 0xFF); /* alpha */
    assert(out[2 * 4 + 2] == 0xFF); /* red (channel index 2 = R in BGRA) */
    assert(out[2 * 4 + 1] == 0x00); /* green */
    assert(out[2 * 4 + 0] == 0x00); /* blue */
    printf("  PASS: test_decode_to_argb8888\n");
}

/* --- Test 4: frame offset indexing ------------------------- */
static void test_frame_offset_indexing(void)
{
    /* Two frames packed: frame0 = 2 pixels, frame1 = 3 pixels */
    static const uint16_t rle[] = {
        /* frame 0: 2px of 0x1234 */
        0x1234, 2,
        /* frame 1: 1x 0xAAAA, 2x 0xBBBB */
        0xAAAA, 1, 0xBBBB, 2,
    };
    /* Offsets in uint16_t words into rle_data.
     * frame 0: 1 pair = 2 words, starts at 0
     * frame 1: 2 pairs = 4 words, starts at 2
     * sentinel: 6
     */
    static const uint32_t offsets[] = { 0, 2, 6 };

    uint16_t frame0[2], frame1[3];
    rle_decode_rgb565(&rle[offsets[0]], frame0, 2);
    rle_decode_rgb565(&rle[offsets[1]], frame1, 3);

    assert(frame0[0] == 0x1234);
    assert(frame0[1] == 0x1234);
    assert(frame1[0] == 0xAAAA);
    assert(frame1[1] == 0xBBBB);
    assert(frame1[2] == 0xBBBB);
    printf("  PASS: test_frame_offset_indexing\n");
}

/* --- Test 5: large run (run_count > 256) ------------------- */
static void test_large_run(void)
{
    static const uint16_t rle[] = { 0x5555, 1000 };
    uint16_t out[1000];
    rle_decode_rgb565(rle, out, 1000);

    for (int i = 0; i < 1000; i++) {
        assert(out[i] == 0x5555);
    }
    printf("  PASS: test_large_run\n");
}

int main(void)
{
    printf("test_rle_decode:\n");
    test_basic_decode();
    test_single_pixel_runs();
    test_decode_to_argb8888();
    test_frame_offset_indexing();
    test_large_run();
    printf("All RLE tests passed.\n");
    return 0;
}
