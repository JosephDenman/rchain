package coop.rchain.rspace

import java.nio.ByteBuffer
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.{FileVisitResult, Files, Path, SimpleFileVisitor}

import coop.rchain.rspace.util.ignore
import scodec.bits.BitVector
import scodec.{Attempt, Codec, DecodeResult}

package object test {

  /**
    * Makes a SimpleFileVisitor to delete files and the directories that contained them
    *
    * [[https://docs.oracle.com/javase/8/docs/api/java/nio/file/FileVisitor.html]]
    */
  private def makeDeleteFileVisitor: SimpleFileVisitor[Path] =
    new SimpleFileVisitor[Path] {
      override def visitFile(p: Path, attrs: BasicFileAttributes): FileVisitResult = {
        Files.delete(p)
        FileVisitResult.CONTINUE
      }
      override def postVisitDirectory(p: Path, e: java.io.IOException): FileVisitResult = {
        Files.delete(p)
        FileVisitResult.CONTINUE
      }
    }

  def recursivelyDeletePath(p: Path): Path =
    Files.walkFileTree(p, makeDeleteFileVisitor)

  /**
    * Converts specified byteBuffer to '-' separated string,
    * convenient during debugging
    */
  private[rspace] def toStr(byteBuffer: ByteBuffer): String = {
    byteBuffer.mark()
    val fetched = new Array[Byte](byteBuffer.remaining())
    ignore { byteBuffer.get(fetched) }
    byteBuffer.reset()
    fetched.toSeq.map(x => x.toString).mkString("-")
  }

  def roundTripCodec[T](t: T)(implicit codec: Codec[T]): Attempt[DecodeResult[T]] =
    codec.encode(t).flatMap((vector: BitVector) => codec.decode(vector))
}
