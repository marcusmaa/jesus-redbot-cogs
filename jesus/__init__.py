from .jesus import Jesus


async def setup(bot):
    await bot.add_cog(Jesus(bot))